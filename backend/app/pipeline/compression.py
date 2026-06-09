from __future__ import annotations

import json
import uuid
from typing import Any

import hdbscan
import numpy as np
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import CompressionError, StageError
from app.db.engine import get_session_factory
from app.db.models import Job
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.llm.client import get_llm_client
from app.pipeline.base import BaseStage
from app.schemas.pipeline import (
    EntityActionLabelResult,
    ToolDefinition,
    UnifiedSchema,
)

logger = get_logger(__name__)

_JACCARD_THRESHOLD = 0.4
_SMALL_API_THRESHOLD = 30


class HybridClusterer:
    """HDBSCAN clustering with entity-grouping fallback for small APIs (<30 ops)."""

    def cluster(
        self,
        embeddings: np.ndarray,
        operation_ids: list[str],
        entities: list[str],
        n_operations: int,
    ) -> list[int]:
        if n_operations < _SMALL_API_THRESHOLD:
            return self._entity_based_clustering(entities)
        return self._hdbscan_clustering(embeddings, n_operations)

    def _hdbscan_clustering(self, embeddings: np.ndarray, n_ops: int) -> list[int]:
        # Normalize embeddings for cosine-equivalent euclidean distance
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normalized = embeddings / norms

        min_cluster_size = max(2, n_ops // 20)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=1,
            metric="euclidean",
            cluster_selection_epsilon=0.15,
            cluster_selection_method="eom",
        )
        return list(clusterer.fit_predict(normalized))

    def _entity_based_clustering(self, entities: list[str]) -> list[int]:
        """Deterministic fallback: group by entity name."""
        entity_to_cluster: dict[str, int] = {}
        labels: list[int] = []
        for entity in entities:
            if entity not in entity_to_cluster:
                entity_to_cluster[entity] = len(entity_to_cluster)
            labels.append(entity_to_cluster[entity])
        return labels


class ClusterValidator:
    """Rejects clusters that would produce semantically incorrect merged tools."""

    def is_valid(self, operations: list[dict[str, Any]]) -> tuple[bool, str]:
        if len(operations) < 2:
            return True, ""

        entities = {op.get("entity", "") for op in operations}
        if len(entities) > 1:
            return False, f"Mixed entities: {entities}"

        methods = {op.get("http_method", "") for op in operations}
        has_delete = "DELETE" in methods
        has_safe = bool(methods & {"GET"})
        if has_delete and has_safe and len(methods) > 1:
            return False, "DELETE mixed with safe methods"

        jaccard = self._schema_jaccard(operations)
        if jaccard < _JACCARD_THRESHOLD and len(operations) > 2:
            return False, f"Schema similarity too low: {jaccard:.2f}"

        return True, ""

    def _schema_jaccard(self, operations: list[dict[str, Any]]) -> float:
        if len(operations) < 2:
            return 1.0
        all_fields: list[set[str]] = []
        for op in operations:
            try:
                schema = json.loads(op.get("param_schema") or "{}")
                all_fields.append(set(schema.keys()))
            except Exception:
                all_fields.append(set())

        if not all_fields or all(len(f) == 0 for f in all_fields):
            return 1.0  # No params — perfectly compatible

        union = set().union(*all_fields)
        intersection = all_fields[0].copy()
        for f in all_fields[1:]:
            intersection &= f

        if not union:
            return 1.0
        return len(intersection) / len(union)


class SchemaUnifier:
    """Merges parameter schemas from multiple operations into one unified tool schema."""

    def unify(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        if len(operations) == 1:
            try:
                return json.loads(operations[0].get("param_schema") or "{}")
            except Exception:
                return {}

        all_schemas: list[dict[str, Any]] = []
        for op in operations:
            try:
                s = json.loads(op.get("param_schema") or "{}")
                if s:
                    all_schemas.append(s)
            except Exception:
                pass

        if not all_schemas:
            return {"type": "object", "properties": {}, "required": []}

        all_fields: dict[str, list[Any]] = {}
        for schema in all_schemas:
            for field, definition in schema.items():
                if field not in all_fields:
                    all_fields[field] = []
                all_fields[field].append(definition)

        n = len(all_schemas)
        properties: dict[str, Any] = {}
        required: list[str] = []
        conflicting: list[str] = []

        for field, definitions in all_fields.items():
            # Field present in all operations → required
            if len(definitions) == n:
                required.append(field)
            # Merge types
            types = list({d.get("type", "string") if isinstance(d, dict) else "string" for d in definitions})
            if len(types) > 1:
                conflicting.append(field)
                properties[field] = {"type": "string", "description": f"Accepts multiple types: {types}"}
            else:
                base_def = definitions[0] if isinstance(definitions[0], dict) else {"type": "string"}
                if field not in required:
                    base_def = {**base_def, "description": (base_def.get("description") or "") + " (optional)"}
                properties[field] = base_def

        result: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            result["required"] = required
        if conflicting:
            result["x-conflicting-fields"] = conflicting

        return result


class CompressionPipeline(BaseStage):
    """Core research contribution: reduces N endpoints → M semantic tools (M << N)."""

    stage_name = "compression"

    def __init__(self) -> None:
        self._neo4j = get_neo4j_client()
        self._queries = GraphQueries(self._neo4j)
        self._llm = get_llm_client()
        self._clusterer = HybridClusterer()
        self._validator = ClusterValidator()
        self._unifier = SchemaUnifier()

    async def execute(self, job_id: str) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if not job:
                raise StageError(self.stage_name, f"Job {job_id} not found")

            config = job.pipeline_config or {}
            if not config.get("enable_compression", True):
                return await self._passthrough(str(job.app_id), job_id)

            app_id = str(job.app_id)

        # Fetch operations with embeddings from Neo4j
        ops_with_embeddings = await self._queries.get_operations_with_embeddings(app_id)
        if not ops_with_embeddings:
            raise StageError(self.stage_name, f"No operations with embeddings for app {app_id}")

        n = len(ops_with_embeddings)
        operation_ids = [row["id"] for row in ops_with_embeddings]
        entities = [row["entity"] for row in ops_with_embeddings]
        embeddings = np.array([row["embedding"] for row in ops_with_embeddings], dtype=np.float32)

        # Run clustering
        labels = self._clusterer.cluster(embeddings, operation_ids, entities, n)

        # Write cluster assignments
        assignments = [
            {"id": op_id, "cluster_id": label, "is_noise": label == -1}
            for op_id, label in zip(operation_ids, labels)
        ]
        await self._queries.set_cluster_assignments(assignments)

        # Group operations by cluster
        clusters: dict[int, list[dict[str, Any]]] = {}
        all_ops = await self._queries.get_operations(app_id)
        op_map = {op["o"]["id"]: op["o"] for op in all_ops}

        for assignment in assignments:
            cluster_id = assignment["cluster_id"]
            op = op_map.get(assignment["id"])
            if op:
                clusters.setdefault(cluster_id, []).append(op)

        # Build tool nodes
        tools_created = 0
        noise_tools = 0

        for cluster_id, cluster_ops in clusters.items():
            is_noise = cluster_id == -1
            if is_noise:
                # Each noise operation becomes its own tool
                for op in cluster_ops:
                    await self._create_single_tool(op, app_id, job_id)
                    noise_tools += 1
            else:
                # Validate cluster before labeling
                valid, reason = self._validator.is_valid(cluster_ops)
                if not valid:
                    logger.info("cluster_rejected", cluster_id=cluster_id, reason=reason)
                    for op in cluster_ops:
                        await self._create_single_tool(op, app_id, job_id)
                        noise_tools += 1
                else:
                    await self._create_merged_tool(cluster_ops, app_id, job_id)
                    tools_created += 1

        total_tools = tools_created + noise_tools
        compression_ratio = n / total_tools if total_tools > 0 else 1.0

        return {
            "operations_clustered": n,
            "clusters_formed": len([c for c in clusters if c != -1]),
            "noise_operations": sum(1 for a in assignments if a["is_noise"]),
            "tools_created": total_tools,
            "compression_ratio": round(compression_ratio, 2),
        }

    async def _passthrough(self, app_id: str, job_id: str) -> dict[str, Any]:
        """Ablation mode: each operation becomes its own tool (no compression)."""
        all_ops = await self._queries.get_operations(app_id)
        for op_record in all_ops:
            await self._create_single_tool(op_record["o"], app_id, job_id)
        return {
            "operations_clustered": len(all_ops),
            "tools_created": len(all_ops),
            "compression_ratio": 1.0,
            "mode": "passthrough",
        }

    async def _create_single_tool(self, op: dict[str, Any], app_id: str, job_id: str) -> None:
        tool_id = str(uuid.uuid4())
        try:
            schema = json.loads(op.get("param_schema") or "{}")
        except Exception:
            schema = {}

        props: dict[str, Any] = {
            "id": tool_id,
            "app_id": app_id,
            "name": op.get("canonical_name") or f"{op.get('action','call').lower()}_{op.get('entity','resource').lower()}",
            "description": op.get("description") or "",
            "unified_schema": json.dumps(schema),
            "permission_scope": [],
            "confidence": op.get("confidence", 1.0),
        }
        await self._queries.create_tool(props, [op["id"]])

    async def _create_merged_tool(
        self, cluster_ops: list[dict[str, Any]], app_id: str, job_id: str
    ) -> None:
        # Label the cluster via LLM
        cluster_summary = [
            {
                "id": op.get("id"),
                "method": op.get("http_method"),
                "path": op.get("http_path"),
                "entity": op.get("entity"),
                "action": op.get("action"),
                "description": op.get("description", "")[:200],
            }
            for op in cluster_ops
        ]

        try:
            label: EntityActionLabelResult = await self._llm.complete_structured(
                prompt_key="entity_action_labeling",
                prompt_vars={
                    "count": len(cluster_ops),
                    "endpoints_json": json.dumps(cluster_summary, indent=2),
                },
                response_model=EntityActionLabelResult,
                job_id=job_id,
                stage=self.stage_name,
            )
        except Exception as exc:
            logger.warning("cluster_label_failed", error=str(exc))
            # Fall back to individual tools
            for op in cluster_ops:
                await self._create_single_tool(op, app_id, job_id)
            return

        unified_schema = self._unifier.unify(cluster_ops)
        tool_id = str(uuid.uuid4())
        op_ids = [op["id"] for op in cluster_ops]

        props: dict[str, Any] = {
            "id": tool_id,
            "app_id": app_id,
            "name": label.canonical_name,
            "description": label.description,
            "unified_schema": json.dumps(unified_schema),
            "permission_scope": [],
            "confidence": label.confidence,
        }
        await self._queries.create_tool(props, op_ids)

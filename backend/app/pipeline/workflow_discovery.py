from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import StageError
from app.db.engine import get_session_factory
from app.db.models import Job
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.llm.client import get_llm_client
from app.pipeline.base import BaseStage
from app.schemas.pipeline import WorkflowHypothesisResult

logger = get_logger(__name__)

_LLM_HYPOTHESIS_CONFIDENCE_FLOOR = 0.5
_STRUCTURAL_CONFIDENCE_BOOST = 0.25
_MIN_WORKFLOW_CONFIDENCE = 0.6


class WorkflowDiscoveryEngine(BaseStage):
    """Detects latent multi-step business workflows in the Capability Graph.

    Uses three signals (in order of reliability):
    1. Response-to-request ID chaining (structural, highest confidence)
    2. Entity lifecycle ordering (rule-based, medium confidence)
    3. LLM workflow hypothesis (probabilistic, lowest confidence — 0.5 floor)
    """

    stage_name = "workflow_discovery"

    def __init__(self) -> None:
        self._neo4j = get_neo4j_client()
        self._queries = GraphQueries(self._neo4j)
        self._llm = get_llm_client()

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
            if not config.get("enable_workflow_discovery", True):
                return {"workflows_created": 0, "mode": "disabled"}

            app_id = str(job.app_id)

        all_ops = await self._queries.get_operations(app_id)
        if not all_ops:
            return {"workflows_created": 0, "precedes_edges": 0}

        operations = [op["o"] for op in all_ops]

        # Signal 1: Response-to-request ID chaining
        id_chain_edges = self._detect_id_chaining(operations)
        for from_id, to_id, confidence in id_chain_edges:
            await self._queries.create_precedes_edge(from_id, to_id, confidence, "id_chaining")

        # Signal 2: Entity lifecycle ordering
        lifecycle_edges = self._detect_lifecycle_ordering(operations)
        for from_id, to_id, confidence in lifecycle_edges:
            await self._queries.create_precedes_edge(from_id, to_id, confidence, "lifecycle")

        # Signal 3: LLM hypothesis
        llm_workflows = await self._llm_hypothesis(operations, job_id)
        for workflow in llm_workflows:
            # Add LLM-hypothesized PRECEDES edges with low confidence
            for i in range(len(workflow["steps"]) - 1):
                await self._queries.create_precedes_edge(
                    workflow["steps"][i],
                    workflow["steps"][i + 1],
                    _LLM_HYPOTHESIS_CONFIDENCE_FLOOR,
                    "llm_hypothesis",
                )

        # Promote high-confidence chains to workflow nodes
        candidates = await self._queries.get_workflow_candidates(
            app_id, min_confidence=_MIN_WORKFLOW_CONFIDENCE
        )

        workflows_created = 0
        op_map = {op["id"]: op for op in operations}

        # Also include LLM-proposed workflows that have enough structural backing
        for candidate in candidates:
            steps = candidate["steps"]
            confidence = candidate["chain_conf"]
            step_ops = [op_map[s] for s in steps if s in op_map]
            if len(step_ops) < 2:
                continue

            wf_name = self._generate_workflow_name(step_ops)
            wf_desc = self._generate_workflow_description(step_ops)
            wf_id = str(uuid.uuid4())

            props: dict[str, Any] = {
                "id": wf_id,
                "app_id": app_id,
                "name": wf_name,
                "description": wf_desc,
                "confidence": round(confidence, 3),
            }

            try:
                await self._queries.create_workflow(props, steps)
                workflows_created += 1
            except Exception as exc:
                logger.warning("workflow_create_failed", error=str(exc))

        total_precedes = len(id_chain_edges) + len(lifecycle_edges)

        return {
            "workflows_created": workflows_created,
            "precedes_edges": total_precedes,
            "llm_hypotheses": len(llm_workflows),
        }

    def _detect_id_chaining(
        self, operations: list[dict[str, Any]]
    ) -> list[tuple[str, str, float]]:
        """If operation A's response includes a field matching operation B's required input → A PRECEDES B."""
        edges: list[tuple[str, str, float]] = []

        for op_a in operations:
            resp_fields = self._extract_response_field_names(op_a)
            if not resp_fields:
                continue

            id_fields = {f for f in resp_fields if f.endswith("_id") or f == "id"}
            if not id_fields:
                continue

            for op_b in operations:
                if op_a["id"] == op_b["id"]:
                    continue
                req_fields = self._extract_request_field_names(op_b)
                overlap = id_fields & req_fields
                if overlap:
                    # Confidence based on overlap size
                    confidence = min(0.95, 0.7 + 0.1 * len(overlap))
                    edges.append((op_a["id"], op_b["id"], confidence))

        return edges

    def _detect_lifecycle_ordering(
        self, operations: list[dict[str, Any]]
    ) -> list[tuple[str, str, float]]:
        """Create precedes POST→GET/PUT/DELETE per entity (creation before use)."""
        edges: list[tuple[str, str, float]] = []
        entity_ops: dict[str, list[dict[str, Any]]] = {}

        for op in operations:
            entity = op.get("entity", "")
            entity_ops.setdefault(entity, []).append(op)

        for entity, ops in entity_ops.items():
            creates = [o for o in ops if o.get("http_method") == "POST" and o.get("action") == "Create"]
            uses = [o for o in ops if o.get("http_method") in ("GET", "PUT", "PATCH", "DELETE")]
            for create in creates:
                for use in uses:
                    if create["id"] != use["id"]:
                        edges.append((create["id"], use["id"], 0.65))

        return edges

    async def _llm_hypothesis(
        self, operations: list[dict[str, Any]], job_id: str
    ) -> list[dict[str, Any]]:
        """Ask LLM to hypothesize workflows from the full operation list."""
        op_summary = [
            {
                "id": op.get("id"),
                "name": op.get("canonical_name") or f"{op.get('action','call').lower()}_{op.get('entity','resource').lower()}",
                "entity": op.get("entity"),
                "action": op.get("action"),
                "method": op.get("http_method"),
            }
            for op in operations
        ]

        try:
            result: WorkflowHypothesisResult = await self._llm.complete_structured(
                prompt_key="workflow_hypothesis",
                prompt_vars={
                    "app_name": "the API",
                    "operations_json": json.dumps(op_summary[:80], indent=2),
                    "max_workflows": min(len(operations) // 3, 10),
                },
                response_model=WorkflowHypothesisResult,
                job_id=job_id,
                stage=self.stage_name,
            )
            return [wf.model_dump() for wf in result.workflows]
        except Exception as exc:
            logger.warning("llm_hypothesis_failed", error=str(exc))
            return []

    def _extract_response_field_names(self, op: dict[str, Any]) -> set[str]:
        try:
            schema = json.loads(op.get("response_schema") or "{}")
            props = schema.get("properties") or {}
            return set(props.keys())
        except Exception:
            return set()

    def _extract_request_field_names(self, op: dict[str, Any]) -> set[str]:
        try:
            schema = json.loads(op.get("param_schema") or "{}")
            return set(schema.keys())
        except Exception:
            return set()

    def _generate_workflow_name(self, steps: list[dict[str, Any]]) -> str:
        if not steps:
            return "workflow"
        first = steps[0]
        action = (first.get("action") or "process").lower()
        entity = (first.get("entity") or "resource").lower()
        return f"{action}_{entity}_workflow"

    def _generate_workflow_description(self, steps: list[dict[str, Any]]) -> str:
        if not steps:
            return ""
        step_names = [
            op.get("canonical_name") or f"{op.get('action','step').lower()}"
            for op in steps
        ]
        return f"Multi-step workflow: {' → '.join(step_names)}"

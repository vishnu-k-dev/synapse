from __future__ import annotations

import json
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
from app.schemas.pipeline import DescriptionResult, NormalizationResult

logger = get_logger(__name__)

_BATCH_SIZE = 20  # operations per embedding batch call


class SemanticEngine(BaseStage):
    """LLM enrichment stage: normalizes names, generates agent-optimized descriptions,
    and stores embeddings in the Capability Graph.

    INVARIANT: LLMs may only write to operation.canonical_name and operation.description.
    No schema, path, method, or graph structure is ever modified by this stage.
    """

    stage_name = "semantic_engine"

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
            app_id = str(job.app_id)

        operations = await self._queries.get_operations(app_id)
        if not operations:
            raise StageError(self.stage_name, f"No operations found for app {app_id}")

        # 1. Normalize names + generate descriptions via LLM
        enriched_count = 0
        for op_record in operations:
            op = op_record["o"]
            try:
                canonical_name, description = await self._enrich_operation(op, job_id)
                await self._queries.set_operation_name_description(
                    operation_id=op["id"],
                    canonical_name=canonical_name,
                    description=description,
                )
                enriched_count += 1
            except Exception as exc:
                logger.warning(
                    "enrich_failed",
                    operation_id=op.get("id"),
                    error=str(exc),
                )
                # Fallback: use rule-based name if LLM fails
                fallback_name = f"{op.get('action', 'call').lower()}_{op.get('entity', 'resource').lower()}"
                await self._queries.set_operation_name_description(
                    operation_id=op["id"],
                    canonical_name=fallback_name,
                    description=op.get("description") or "",
                )

        # 2. Build rich text representations and embed in batch
        embedding_count = await self._embed_operations(app_id, operations, job_id)

        return {
            "operations_enriched": enriched_count,
            "embeddings_created": embedding_count,
        }

    async def _enrich_operation(self, op: dict[str, Any], job_id: str) -> tuple[str, str]:
        entity = op.get("entity", "Resource")
        action = op.get("action", "Call")
        method = op.get("http_method", "GET")
        path = op.get("http_path", "/")
        original_desc = op.get("description") or ""

        # Normalize name
        synonyms = [op.get("operation_id") or "", f"{action.lower()}_{entity.lower()}"]
        synonyms = [s for s in synonyms if s]

        norm_result: NormalizationResult = await self._llm.complete_structured(
            prompt_key="normalization",
            prompt_vars={
                "names_json": json.dumps(synonyms),
                "entity": entity,
                "action": action,
            },
            response_model=NormalizationResult,
            job_id=job_id,
            stage=self.stage_name,
        )
        canonical_name = norm_result.canonical_name

        # Parse param/response schema for description prompt
        try:
            param_schema = json.loads(op.get("param_schema") or "{}")
            param_names = list(param_schema.keys())[:5]
        except Exception:
            param_names = []

        try:
            resp_schema = json.loads(op.get("response_schema") or "{}")
            resp_fields = list((resp_schema.get("properties") or {}).keys())[:5]
        except Exception:
            resp_fields = []

        desc_result: DescriptionResult = await self._llm.complete_structured(
            prompt_key="description_generation",
            prompt_vars={
                "tool_name": canonical_name,
                "entity": entity,
                "action": action,
                "http_method": method,
                "http_path": path,
                "original_description": original_desc[:300],
                "parameters": ", ".join(param_names) or "none",
                "response_summary": ", ".join(resp_fields) or "unknown",
            },
            response_model=DescriptionResult,
            job_id=job_id,
            stage=self.stage_name,
        )

        return canonical_name, desc_result.description

    async def _embed_operations(
        self,
        app_id: str,
        operations: list[dict[str, Any]],
        job_id: str,
    ) -> int:
        """Serialize operations to rich text, embed in batch, store in Neo4j."""
        ids = [op["o"]["id"] for op in operations]
        texts = [self._operation_to_rich_text(op["o"]) for op in operations]

        embedded_count = 0
        for i in range(0, len(texts), _BATCH_SIZE):
            batch_ids = ids[i : i + _BATCH_SIZE]
            batch_texts = texts[i : i + _BATCH_SIZE]

            embeddings = await self._llm.embed_batch(
                texts=batch_texts, job_id=job_id, stage=self.stage_name
            )

            for op_id, embedding in zip(batch_ids, embeddings):
                await self._queries.set_operation_embedding(op_id, embedding)
                embedded_count += 1

        return embedded_count

    def _operation_to_rich_text(self, op: dict[str, Any]) -> str:
        """Serialize operation to rich text for embedding — the representation matters."""
        method = op.get("http_method", "")
        path = op.get("http_path", "")
        entity = op.get("entity", "")
        action = op.get("action", "")
        desc = op.get("description") or ""

        try:
            param_schema = json.loads(op.get("param_schema") or "{}")
            input_fields = list(param_schema.keys())
        except Exception:
            input_fields = []

        try:
            resp_schema = json.loads(op.get("response_schema") or "{}")
            output_fields = list((resp_schema.get("properties") or {}).keys())
        except Exception:
            output_fields = []

        return (
            f"{method} {path} — {desc}\n"
            f"Entity: {entity}. Action: {action}.\n"
            f"Inputs: {', '.join(input_fields) or 'none'}.\n"
            f"Outputs: {', '.join(output_fields) or 'unknown'}."
        )

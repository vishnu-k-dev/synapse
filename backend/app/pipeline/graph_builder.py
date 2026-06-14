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
from app.pipeline.base import BaseStage
from app.schemas.pipeline import AnnotatedApplicationModel, AnnotatedEndpoint

logger = get_logger(__name__)


class CapabilityGraphBuilder(BaseStage):
    """Constructs the Capability Graph in Neo4j from the annotated application model."""

    stage_name = "graph_builder"

    def __init__(self) -> None:
        self._neo4j = get_neo4j_client()
        self._queries = GraphQueries(self._neo4j)

    async def execute(self, job_id: str) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if not job or not job.annotated_model:
                raise StageError(self.stage_name, "Annotated model not found on job")
            model = AnnotatedApplicationModel(**job.annotated_model)

        # Ensure indexes exist before writing
        await self._neo4j.ensure_indexes()

        # 1. Collect all unique entities
        entities = self._collect_entities(model)
        for entity_name, entity_data in entities.items():
            await self._queries.upsert_entity(
                app_id=model.app_id,
                name=entity_name,
                plural=entity_data["plural"],
                description="",
                source_tags=entity_data["tags"],
            )

        # 2. Create operations
        operation_count = 0
        for endpoint in model.endpoints:
            props = self._endpoint_to_operation_props(endpoint, model.app_id)
            try:
                await self._queries.create_operation(props)
                operation_count += 1
            except Exception as exc:
                logger.warning(
                    "operation_create_skipped",
                    path=endpoint.path,
                    method=endpoint.method,
                    error=str(exc),
                )

        # 3. Create ownership relationships
        relationship_count = 0
        seen_relationships = set()
        for relationship in model.relationships:
            key = (relationship.owner, relationship.owned)
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            try:
                await self._queries.create_ownership(
                    app_id=model.app_id,
                    owner=relationship.owner,
                    owned=relationship.owned,
                    cardinality=relationship.cardinality,
                )
                relationship_count += 1
            except Exception as exc:
                logger.warning("relationship_create_failed", error=str(exc))

        stats = await self._queries.get_graph_stats(model.app_id)

        return {
            "entities_created": len(entities),
            "operations_created": operation_count,
            "relationships_created": relationship_count,
            "graph_stats": stats,
        }

    def _collect_entities(self, model: AnnotatedApplicationModel) -> dict[str, dict[str, Any]]:
        """Build a map of entity_name → {plural, tags} from all endpoints."""
        entities: dict[str, dict[str, Any]] = {}
        import inflect
        engine = inflect.engine()

        for endpoint in model.endpoints:
            for entity_name in self._entities_from_endpoint(endpoint):
                if entity_name not in entities:
                    plural = engine.plural(entity_name.lower()) or f"{entity_name.lower()}s"
                    entities[entity_name] = {"plural": plural, "tags": []}
                if endpoint.tags:
                    entities[entity_name]["tags"] = list(
                        set(entities[entity_name]["tags"]) | set(endpoint.tags)
                    )

        return entities

    def _entities_from_endpoint(self, endpoint: AnnotatedEndpoint) -> list[str]:
        result = [endpoint.entity]
        if endpoint.parent_entity:
            result.append(endpoint.parent_entity)
        for rel in endpoint.inferred_relationships:
            result.extend([rel.owner, rel.owned])
        return list(dict.fromkeys(result))  # preserve order, deduplicate

    def _endpoint_to_operation_props(self, endpoint: AnnotatedEndpoint, app_id: str) -> dict[str, Any]:
        return {
            "id": endpoint.id,
            "app_id": app_id,
            "http_method": endpoint.method,
            "http_path": endpoint.path,
            "operation_id": endpoint.operation_id or "",
            "entity": endpoint.entity,
            "action": endpoint.action,
            "description": endpoint.description or "",
            "param_schema": json.dumps(
                {p.name: p.schema_ for p in endpoint.parameters if p.schema_}
            ),
            "response_schema": json.dumps(endpoint.response_schema or {}),
            "routing": json.dumps(self._build_routing(endpoint)),
            "confidence": endpoint.extraction_confidence,
        }

    def _build_routing(self, endpoint: AnnotatedEndpoint) -> dict[str, Any]:
        """Capture everything needed to make the real HTTP call: method, path,
        and parameters split by location (path / query / body). The synthesizer
        uses this to render a working request instead of a placeholder GET /."""
        path_params: list[dict[str, Any]] = []
        query_params: list[dict[str, Any]] = []
        for p in endpoint.parameters:
            location = (p.location or "query").lower()
            schema = p.schema_ if isinstance(p.schema_, dict) else {"type": "string"}
            entry = {"name": p.name, "schema": schema, "required": bool(p.required)}
            if location == "path":
                entry["required"] = True
                path_params.append(entry)
            elif location == "query":
                query_params.append(entry)
            # header/cookie params are intentionally not exposed as tool args

        body_params: list[dict[str, Any]] = []
        body = endpoint.request_schema if isinstance(endpoint.request_schema, dict) else {}
        props = body.get("properties") if isinstance(body, dict) else None
        if isinstance(props, dict):
            required = set(body.get("required") or [])
            for name, schema in props.items():
                body_params.append({
                    "name": name,
                    "schema": schema if isinstance(schema, dict) else {"type": "string"},
                    "required": name in required,
                })

        return {
            "method": endpoint.method,
            "path": endpoint.path,
            "path_params": path_params,
            "query_params": query_params,
            "body_params": body_params,
            "has_body": bool(body_params),
        }

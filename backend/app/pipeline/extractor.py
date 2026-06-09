from __future__ import annotations

import re
import uuid
from typing import Any

import inflect
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import StageError
from app.db.engine import get_session_factory
from app.db.models import Job
from app.llm.client import get_llm_client
from app.pipeline.base import BaseStage
from app.schemas.pipeline import (
    ACTION_TAXONOMY,
    DOMAIN_VERB_OVERRIDES,
    ENTITY_TAXONOMY,
    HTTP_METHOD_DEFAULT_ACTION,
    AnnotatedApplicationModel,
    AnnotatedEndpoint,
    EntityExtractionResult,
    Relationship,
    RawApplicationModel,
    RawEndpoint,
)

logger = get_logger(__name__)
_inflect = inflect.engine()


# ── Path noun extraction ──────────────────────────────────────────────────────

_PATH_SEGMENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def _singularize(word: str) -> str:
    singular = _inflect.singular_noun(word)
    return singular if singular else word


def _to_pascal_case(word: str) -> str:
    return word.replace("-", "_").replace(" ", "_").title().replace("_", "")


def extract_path_nouns(path: str) -> list[str]:
    """Extract resource nouns from a URL path, skipping path params and version segments."""
    segments = [s for s in path.split("/") if s]
    nouns: list[str] = []
    for seg in segments:
        if seg.startswith("{") and seg.endswith("}"):
            continue
        if re.match(r"^v\d+$", seg, re.IGNORECASE):
            continue
        if re.match(r"^api$", seg, re.IGNORECASE):
            continue
        if _PATH_SEGMENT_RE.match(seg):
            noun = _singularize(seg.lower().replace("-", "_"))
            nouns.append(noun)
    return nouns


def primary_entity_from_path(path: str) -> tuple[str | None, str | None]:
    """Returns (leaf_entity_pascal, parent_entity_pascal)."""
    nouns = extract_path_nouns(path)
    if not nouns:
        return None, None
    leaf = _to_pascal_case(nouns[-1])
    parent = _to_pascal_case(nouns[-2]) if len(nouns) >= 2 else None
    return leaf, parent


def extract_relationships_from_path(path: str) -> list[Relationship]:
    """Infer ownership chain from path structure.
    /customers/{id}/orders/{id}/items → Customer OWNS Order OWNS Item
    """
    nouns = extract_path_nouns(path)
    relationships: list[Relationship] = []
    for i in range(len(nouns) - 1):
        owner = _to_pascal_case(nouns[i])
        owned = _to_pascal_case(nouns[i + 1])
        relationships.append(Relationship(owner=owner, owned=owned, type="OWNS"))
    return relationships


# ── Action classification ─────────────────────────────────────────────────────

def _path_ends_with(path: str, suffixes: list[str]) -> str | None:
    last_segment = path.rstrip("/").split("/")[-1].lower()
    for suffix in suffixes:
        if last_segment == suffix or last_segment.endswith(f"/{suffix}"):
            return suffix
    return None


def classify_action(method: str, path: str) -> str:
    """Classify HTTP method + path into a canonical action from ACTION_TAXONOMY."""
    method = method.upper()
    path_lower = path.lower()

    # Check path-terminal domain verbs first (most specific)
    last_segment = path.rstrip("/").split("/")[-1].lower().replace("-", "_")
    if last_segment in DOMAIN_VERB_OVERRIDES:
        return DOMAIN_VERB_OVERRIDES[last_segment]

    # GET collection vs GET single resource
    if method == "GET":
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        params = [s for s in path.split("/") if s.startswith("{")]
        last_non_param = extract_path_nouns(path)
        # If last meaningful segment is a collection (plural noun) and no ID follows → List
        if last_non_param:
            raw_last = [s for s in path.split("/") if s and not s.startswith("{")][-1].lower()
            is_plural = bool(_inflect.singular_noun(raw_last))
            has_trailing_id = path.rstrip("/").split("/")[-1].startswith("{")
            if is_plural and not has_trailing_id:
                return "List"
        return "Read"

    return HTTP_METHOD_DEFAULT_ACTION.get(method, "Read")


# ── Main extractor ────────────────────────────────────────────────────────────

class CapabilityExtractor(BaseStage):
    stage_name = "extractor"

    def __init__(self) -> None:
        self._llm = get_llm_client()

    async def execute(self, job_id: str) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if not job or not job.raw_model:
                raise StageError(self.stage_name, "Raw model not found on job")
            raw = RawApplicationModel(**job.raw_model)

        annotated_endpoints: list[AnnotatedEndpoint] = []
        all_relationships: list[Relationship] = []

        for endpoint in raw.endpoints:
            annotated = await self._annotate(endpoint, job_id)
            annotated_endpoints.append(annotated)
            for rel in annotated.inferred_relationships:
                if rel not in all_relationships:
                    all_relationships.append(rel)

        model = AnnotatedApplicationModel(
            app_id=raw.app_id,
            endpoints=annotated_endpoints,
            relationships=all_relationships,
        )

        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one()
            job.annotated_model = model.model_dump()
            await session.commit()

        llm_fallback_count = sum(1 for e in annotated_endpoints if e.extraction_confidence < 0.9)

        return {
            "endpoint_count": len(annotated_endpoints),
            "relationship_count": len(all_relationships),
            "llm_fallback_count": llm_fallback_count,
        }

    async def _annotate(self, endpoint: RawEndpoint, job_id: str) -> AnnotatedEndpoint:
        entity, parent_entity = self._extract_entity_rule_based(endpoint)
        confidence = 1.0

        if entity is None:
            entity, confidence = await self._extract_entity_llm(endpoint, job_id)
            parent_entity = None

        action = classify_action(endpoint.method, endpoint.path)
        relationships = extract_relationships_from_path(endpoint.path)

        return AnnotatedEndpoint(
            **endpoint.model_dump(),
            entity=entity or "Unknown",
            action=action,
            parent_entity=parent_entity,
            extraction_confidence=confidence,
            inferred_relationships=relationships,
        )

    def _extract_entity_rule_based(self, endpoint: RawEndpoint) -> tuple[str | None, str | None]:
        # Try tags first — OpenAPI tags are often entity names
        if endpoint.tags:
            tag = endpoint.tags[0]
            pascal = _to_pascal_case(tag)
            return pascal, None

        # Fall back to path noun extraction
        leaf, parent = primary_entity_from_path(endpoint.path)
        return leaf, parent

    async def _extract_entity_llm(self, endpoint: RawEndpoint, job_id: str) -> tuple[str, float]:
        try:
            result: EntityExtractionResult = await self._llm.complete_structured(
                prompt_key="entity_extraction",
                prompt_vars={
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "description": endpoint.description or "",
                    "tags": ", ".join(endpoint.tags),
                },
                response_model=EntityExtractionResult,
                job_id=job_id,
                stage=self.stage_name,
            )
            return result.entity, result.confidence
        except Exception as exc:
            logger.warning("entity_llm_fallback_failed", error=str(exc), path=endpoint.path)
            return "Unknown", 0.3

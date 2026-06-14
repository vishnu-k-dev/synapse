from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from celery import chain

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


def _run(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task.

    Each task gets a fresh event loop via asyncio.run(). Global async singletons
    (SQLAlchemy engine, Neo4j driver, OpenAI/Anthropic HTTP clients) bind to the
    loop that first uses them, so they must be disposed before the loop closes —
    otherwise the next task hits "Future attached to a different loop".
    """
    async def _wrapper() -> Any:
        try:
            return await coro
        finally:
            from app.db.engine import close_engine
            from app.graph.client import close_driver
            from app.llm.client import close_llm_client
            await close_engine()
            await close_driver()
            await close_llm_client()

    return asyncio.run(_wrapper())


# ── Individual stage tasks ────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_discovery")
def run_discovery(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.discovery import DiscoveryEngine
    try:
        return _run(DiscoveryEngine().run(job_id))
    except Exception as exc:
        logger.error("task_failed", task="discovery", job_id=job_id, error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_extractor")
def run_extractor(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.extractor import CapabilityExtractor
    try:
        return _run(CapabilityExtractor().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_graph_builder")
def run_graph_builder(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.graph_builder import CapabilityGraphBuilder
    try:
        return _run(CapabilityGraphBuilder().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_semantic_engine")
def run_semantic_engine(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.semantic_engine import SemanticEngine
    try:
        return _run(SemanticEngine().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_compression")
def run_compression(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.compression import CompressionPipeline
    try:
        return _run(CompressionPipeline().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_workflow_discovery")
def run_workflow_discovery(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.workflow_discovery import WorkflowDiscoveryEngine
    try:
        return _run(WorkflowDiscoveryEngine().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10, name="app.tasks.pipeline.run_synthesizer")
def run_synthesizer(self: Any, job_id: str) -> dict[str, Any]:
    from app.pipeline.synthesizer import SynthesisEngine
    try:
        return _run(SynthesisEngine().run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc)


# ── Pipeline dispatch ─────────────────────────────────────────────────────────

def dispatch_pipeline(job_id: str, resume_from: str | None = None) -> str:
    """Dispatch the full 7-stage pipeline as a Celery chain.

    resume_from: if set, skip all stages before this one (for development iteration).
    Returns the Celery task ID of the chain.
    """
    all_stages = [
        ("discovery", run_discovery),
        ("extractor", run_extractor),
        ("graph_builder", run_graph_builder),
        ("semantic_engine", run_semantic_engine),
        ("compression", run_compression),
        ("workflow_discovery", run_workflow_discovery),
        ("synthesizer", run_synthesizer),
    ]

    if resume_from:
        stage_names = [s[0] for s in all_stages]
        if resume_from in stage_names:
            start_idx = stage_names.index(resume_from)
            all_stages = all_stages[start_idx:]
        else:
            logger.warning("unknown_resume_stage", stage=resume_from)

    tasks = [task.si(job_id) for _, task in all_stages]
    pipeline = chain(*tasks)
    result = pipeline.apply_async()

    logger.info("pipeline_dispatched", job_id=job_id, task_id=result.id)
    return result.id

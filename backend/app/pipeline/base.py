from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import bind_job_context, get_logger
from app.core.security import StageError
from app.db.engine import get_session_factory
from app.db.models import Job, PipelineEvent

logger = get_logger(__name__)


class StageProtocol(Protocol):
    """Every pipeline stage must conform to this interface."""

    stage_name: str

    async def execute(self, job_id: str) -> dict[str, Any]:
        """Run the stage. Returns a summary dict (logged to pipeline_events)."""
        ...


class BaseStage:
    """Mixin providing job state management for all pipeline stages."""

    stage_name: str = "unknown"

    async def run(self, job_id: str) -> dict[str, Any]:
        """Entry point — wraps execute() with event logging."""
        bind_job_context(job_id, self.stage_name)
        logger.info("stage_start")

        await self._emit_event(job_id, "running")

        try:
            summary = await self.execute(job_id)
            await self._emit_event(job_id, "complete", summary=summary)
            logger.info("stage_complete", summary=summary)
            return summary

        except StageError:
            raise

        except Exception as exc:
            error_msg = str(exc)
            await self._emit_event(job_id, "failed", error_message=error_msg)
            logger.error("stage_failed", error=error_msg, exc_info=True)
            raise StageError(self.stage_name, error_msg, cause=exc) from exc

    async def execute(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def get_job(self, session: AsyncSession, job_id: str) -> Job:
        result = await session.execute(
            select(Job).where(Job.id == uuid.UUID(job_id))
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise StageError(self.stage_name, f"Job {job_id} not found")
        return job

    async def _emit_event(
        self,
        job_id: str,
        status: str,
        summary: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.now(timezone.utc)

            # Check for existing running event for this stage
            result = await session.execute(
                select(PipelineEvent)
                .where(PipelineEvent.job_id == uuid.UUID(job_id))
                .where(PipelineEvent.stage == self.stage_name)
                .where(PipelineEvent.status == "running")
            )
            existing = result.scalar_one_or_none()

            if existing and status in ("complete", "failed"):
                existing.status = status
                existing.completed_at = now
                existing.summary = summary
                existing.error_message = error_message
            else:
                event = PipelineEvent(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    stage=self.stage_name,
                    status=status,
                    summary=summary,
                    error_message=error_message,
                    started_at=now,
                    completed_at=now if status in ("complete", "failed") else None,
                )
                session.add(event)

            await session.commit()

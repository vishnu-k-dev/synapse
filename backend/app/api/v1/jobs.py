from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.engine import get_db, get_session_factory
from app.db.models import Job, PipelineEvent
from app.schemas.api import JobStatusResponse, PipelineEventSchema

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    events_result = await db.execute(
        select(PipelineEvent)
        .where(PipelineEvent.job_id == job_id)
        .order_by(PipelineEvent.started_at)
    )
    events = events_result.scalars().all()

    artifact_url = None
    if job.artifact_object_key:
        from app.storage.minio import get_minio_client
        try:
            minio = get_minio_client()
            artifact_url = await minio.get_presigned_url(
                minio.bucket_artifacts, job.artifact_object_key
            )
        except Exception:
            pass

    return JobStatusResponse(
        job_id=job.id,
        app_id=job.app_id,
        status=job.status,
        pipeline_config=job.pipeline_config or {},
        events=[
            PipelineEventSchema(
                stage=e.stage,
                status=e.status,
                summary=e.summary,
                error_message=e.error_message,
                started_at=e.started_at,
                completed_at=e.completed_at,
            )
            for e in events
        ],
        artifact_url=artifact_url,
        error_message=job.error_message,
        created_at=job.created_at,
    )


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    _: str = Depends(require_api_key),
) -> StreamingResponse:
    """Server-Sent Events stream — polls pipeline_events every 2 seconds."""

    async def _event_generator() -> AsyncIterator[str]:
        seen_event_ids: set[uuid.UUID] = set()
        terminal_statuses = {"complete", "failed", "cancelled"}

        for _ in range(300):  # max 10 minutes
            factory = get_session_factory()
            async with factory() as session:
                job_result = await session.execute(
                    select(Job).where(Job.id == job_id)
                )
                job = job_result.scalar_one_or_none()
                if not job:
                    yield "event: error\ndata: {\"detail\": \"Job not found\"}\n\n"
                    return

                events_result = await session.execute(
                    select(PipelineEvent)
                    .where(PipelineEvent.job_id == job_id)
                    .order_by(PipelineEvent.started_at)
                )
                events = events_result.scalars().all()

                for event in events:
                    if event.id not in seen_event_ids:
                        seen_event_ids.add(event.id)
                        data = json.dumps({
                            "stage": event.stage,
                            "status": event.status,
                            "summary": event.summary,
                            "error": event.error_message,
                        })
                        yield f"event: pipeline_event\ndata: {data}\n\n"

                if job.status in terminal_statuses:
                    yield f"event: job_complete\ndata: {{\"status\": \"{job.status}\"}}\n\n"
                    return

            await asyncio.sleep(2)

        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

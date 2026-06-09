from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.engine import get_db
from app.db.models import Job
from app.schemas.api import SynthesisRequest, SynthesisResponse
from app.schemas.pipeline import PipelineConfig, SynthesisTarget
from app.tasks.pipeline import dispatch_pipeline

router = APIRouter(prefix="/synthesis", tags=["synthesis"])


@router.post("/{app_id}", response_model=SynthesisResponse)
async def trigger_synthesis(
    app_id: uuid.UUID,
    request: SynthesisRequest,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SynthesisResponse:
    """Re-trigger synthesis stage only (useful for changing target language or permission scopes)."""
    job_result = await db.execute(
        select(Job)
        .where(Job.app_id == app_id)
        .where(Job.status == "complete")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    completed_job = job_result.scalar_one_or_none()
    if not completed_job:
        raise HTTPException(
            status_code=404,
            detail="No completed job found. Run the full pipeline first.",
        )

    # Create a new synthesis-only job
    new_job_id = uuid.uuid4()
    config = {
        "synthesis_target": request.target.value,
        "permission_scopes": request.permission_scopes,
        "enable_compression": True,
        "enable_workflow_discovery": True,
    }

    new_job = Job(
        id=new_job_id,
        app_id=app_id,
        status="pending",
        pipeline_config=config,
    )
    new_job.status = "running"
    new_job.started_at = datetime.now(timezone.utc)
    db.add(new_job)
    await db.commit()

    dispatch_pipeline(str(new_job_id), resume_from="synthesizer")

    return SynthesisResponse(
        job_id=new_job_id,
        message="Synthesis job started",
    )


@router.get("/{app_id}/download")
async def download_artifact(
    app_id: uuid.UUID,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    job_result = await db.execute(
        select(Job)
        .where(Job.app_id == app_id)
        .where(Job.status == "complete")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()
    if not job or not job.artifact_object_key:
        raise HTTPException(status_code=404, detail="No artifact found for this application")

    from app.storage.minio import get_minio_client
    minio = get_minio_client()
    url = await minio.get_presigned_url(minio.bucket_artifacts, job.artifact_object_key)
    return {"download_url": url, "expires_in_seconds": 3600}

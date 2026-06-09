from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.core.security import CredentialEncryptor
from app.core.config import get_settings
from app.db.engine import get_db
from app.db.models import Application, Job
from app.schemas.api import AppSummary, CreateAppRequest, CreateAppResponse
from app.schemas.pipeline import PipelineConfig
from app.storage.minio import get_minio_client
from app.tasks.pipeline import dispatch_pipeline

router = APIRouter(prefix="/apps", tags=["applications"])


@router.post("", response_model=CreateAppResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    request: CreateAppRequest,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> CreateAppResponse:
    settings = get_settings()
    encryptor = CredentialEncryptor(settings.encryption_key_bytes)

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()

    encrypted_cred = None
    if request.auth_credential:
        encrypted_cred = encryptor.encrypt(request.auth_credential)

    application = Application(
        id=app_id,
        name=request.name,
        description=request.description,
        base_url=request.base_url,
        auth_type=request.auth_type,
        auth_credentials_encrypted=encrypted_cred,
        source_format="openapi3",
    )
    db.add(application)

    job = Job(
        id=job_id,
        app_id=app_id,
        status="pending",
        pipeline_config=request.pipeline_config.model_dump(),
    )
    db.add(job)
    await db.commit()

    return CreateAppResponse(app_id=app_id, job_id=job_id)


@router.post("/{app_id}/spec")
async def upload_spec(
    app_id: uuid.UUID,
    file: UploadFile,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Upload spec file and trigger pipeline."""
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Spec file too large (max 10 MB)")

    # Detect format from filename
    filename = file.filename or ""
    if "postman" in filename.lower() or filename.endswith(".postman_collection.json"):
        source_format = "postman21"
    else:
        source_format = "openapi3"

    # Store spec in MinIO
    minio = get_minio_client()
    object_key = f"specs/{app_id}/{filename}"
    await minio.put_object(
        bucket=minio.bucket_specs,
        key=object_key,
        data=content,
        content_type=file.content_type or "application/octet-stream",
    )

    app.spec_object_key = object_key
    app.source_format = source_format
    await db.commit()

    # Find pending job and start pipeline
    job_result = await db.execute(
        select(Job)
        .where(Job.app_id == app_id)
        .where(Job.status == "pending")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=409, detail="No pending job found for this application")

    dispatch_pipeline(str(job.id))
    return {"message": "Spec uploaded and pipeline started", "job_id": str(job.id)}


@router.get("", response_model=list[AppSummary])
async def list_applications(
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> list[AppSummary]:
    result = await db.execute(select(Application).order_by(Application.created_at.desc()))
    apps = result.scalars().all()
    return [
        AppSummary(
            id=a.id,
            name=a.name,
            description=a.description,
            source_format=a.source_format,
            created_at=a.created_at,
        )
        for a in apps
    ]

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Application ───────────────────────────────────────────────────────────────

class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(String(1024))
    auth_type: Mapped[str | None] = mapped_column(
        Enum("bearer", "api_key", "oauth2", "none", name="auth_type_enum")
    )
    auth_credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    spec_object_key: Mapped[str | None] = mapped_column(String(512))
    source_format: Mapped[str | None] = mapped_column(
        Enum("openapi3", "postman21", name="source_format_enum")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    jobs: Mapped[list[Job]] = relationship("Job", back_populates="application", lazy="select")


# ── Job ───────────────────────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "complete", "failed", "cancelled", name="job_status_enum"),
        default="pending",
        nullable=False,
    )
    pipeline_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_model: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    annotated_model: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    artifact_object_key: Mapped[str | None] = mapped_column(String(512))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[Application] = relationship("Application", back_populates="jobs")
    events: Mapped[list[PipelineEvent]] = relationship(
        "PipelineEvent", back_populates="job", lazy="select"
    )


# ── Pipeline Event ────────────────────────────────────────────────────────────

class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    stage: Mapped[str] = mapped_column(
        Enum(
            "discovery",
            "extractor",
            "graph_builder",
            "semantic_engine",
            "compression",
            "workflow_discovery",
            "synthesizer",
            name="pipeline_stage_enum",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("running", "complete", "failed", name="event_status_enum"),
        nullable=False,
    )
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship("Job", back_populates="events")


# ── LLM Call Log ──────────────────────────────────────────────────────────────

class LLMCallLog(Base):
    """Every LLM call — the research reproducibility artifact."""

    __tablename__ = "llm_call_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_key: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100))
    experiment_id: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""Initial schema: applications, jobs, pipeline_events, llm_call_log

Revision ID: 001
Revises:
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enum types ────────────────────────────────────────────────────────────
    op.execute("CREATE TYPE auth_type_enum AS ENUM ('bearer','api_key','oauth2','none')")
    op.execute("CREATE TYPE source_format_enum AS ENUM ('openapi3','postman21')")
    op.execute("CREATE TYPE job_status_enum AS ENUM ('pending','running','complete','failed','cancelled')")
    op.execute(
        "CREATE TYPE pipeline_stage_enum AS ENUM "
        "('discovery','extractor','graph_builder','semantic_engine','compression','workflow_discovery','synthesizer')"
    )
    op.execute("CREATE TYPE event_status_enum AS ENUM ('running','complete','failed')")

    # ── applications ──────────────────────────────────────────────────────────
    # create_type=False: enum types are created manually above via op.execute;
    # without this, sa.Enum re-emits CREATE TYPE and conflicts within the txn.
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("base_url", sa.String(1024)),
        sa.Column("auth_type", postgresql.ENUM("bearer", "api_key", "oauth2", "none", name="auth_type_enum", create_type=False), nullable=True),
        sa.Column("auth_credentials_encrypted", sa.Text),
        sa.Column("spec_object_key", sa.String(512)),
        sa.Column("source_format", postgresql.ENUM("openapi3", "postman21", name="source_format_enum", create_type=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("status", postgresql.ENUM("pending","running","complete","failed","cancelled", name="job_status_enum", create_type=False), nullable=False, server_default="pending"),
        sa.Column("pipeline_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_model", postgresql.JSONB),
        sa.Column("annotated_model", postgresql.JSONB),
        sa.Column("artifact_object_key", sa.String(512)),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_jobs_app_id", "jobs", ["app_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    # ── pipeline_events ───────────────────────────────────────────────────────
    op.create_table(
        "pipeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("stage", postgresql.ENUM("discovery","extractor","graph_builder","semantic_engine","compression","workflow_discovery","synthesizer", name="pipeline_stage_enum", create_type=False), nullable=False),
        sa.Column("status", postgresql.ENUM("running","complete","failed", name="event_status_enum", create_type=False), nullable=False),
        sa.Column("summary", postgresql.JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_pipeline_events_job_id", "pipeline_events", ["job_id"])

    # ── llm_call_log ──────────────────────────────────────────────────────────
    op.create_table(
        "llm_call_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("prompt_key", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_type", sa.String(100)),
        sa.Column("experiment_id", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_llm_call_log_job_id", "llm_call_log", ["job_id"])
    op.create_index("ix_llm_call_log_prompt_key", "llm_call_log", ["prompt_key", "prompt_version"])


def downgrade() -> None:
    op.drop_table("llm_call_log")
    op.drop_table("pipeline_events")
    op.drop_table("jobs")
    op.drop_table("applications")
    op.execute("DROP TYPE IF EXISTS event_status_enum")
    op.execute("DROP TYPE IF EXISTS pipeline_stage_enum")
    op.execute("DROP TYPE IF EXISTS job_status_enum")
    op.execute("DROP TYPE IF EXISTS source_format_enum")
    op.execute("DROP TYPE IF EXISTS auth_type_enum")

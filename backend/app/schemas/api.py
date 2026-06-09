"""FastAPI request/response schemas for all API endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.pipeline import PipelineConfig, SynthesisTarget


# ── Application ───────────────────────────────────────────────────────────────

class CreateAppRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    base_url: str | None = None
    auth_type: str | None = None
    auth_credential: str | None = Field(default=None, description="Plaintext; stored encrypted")
    pipeline_config: PipelineConfig = Field(default_factory=PipelineConfig)


class CreateAppResponse(BaseModel):
    app_id: UUID
    job_id: UUID
    message: str = "Pipeline started"


class AppSummary(BaseModel):
    id: UUID
    name: str
    description: str | None
    source_format: str | None
    created_at: datetime


# ── Job / Pipeline ────────────────────────────────────────────────────────────

class PipelineEventSchema(BaseModel):
    stage: str
    status: str
    summary: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class JobStatusResponse(BaseModel):
    job_id: UUID
    app_id: UUID
    status: str
    pipeline_config: dict[str, Any]
    events: list[PipelineEventSchema] = Field(default_factory=list)
    artifact_url: str | None = None
    error_message: str | None = None
    created_at: datetime


# ── Graph ─────────────────────────────────────────────────────────────────────

class GraphNodeSchema(BaseModel):
    id: str
    label: str
    node_type: str
    properties: dict[str, Any]


class GraphEdgeSchema(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    app_id: str
    nodes: list[GraphNodeSchema]
    edges: list[GraphEdgeSchema]
    stats: dict[str, int]


class UpdateNodeRequest(BaseModel):
    canonical_name: str | None = None
    description: str | None = None
    entity: str | None = None
    action: str | None = None


# ── Tools ─────────────────────────────────────────────────────────────────────

class ToolSchema(BaseModel):
    id: str
    name: str
    entity: str
    action: str
    description: str
    source_endpoint_count: int
    compression_ratio: float
    is_workflow: bool
    confidence: float
    permission_scope: list[str]
    operation_ids: list[str]


class UpdateToolRequest(BaseModel):
    name: str | None = None
    entity: str | None = None
    action: str | None = None
    description: str | None = None
    permission_scope: list[str] | None = None


class MergeToolsRequest(BaseModel):
    tool_ids: list[str] = Field(..., min_length=2)


class SplitToolRequest(BaseModel):
    tool_id: str


# ── Workflows ─────────────────────────────────────────────────────────────────

class WorkflowStepSchema(BaseModel):
    operation_id: str
    step_index: int
    required: bool


class WorkflowSchema(BaseModel):
    id: str
    name: str
    description: str
    confidence: float
    steps: list[WorkflowStepSchema]


class UpdateWorkflowRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list[WorkflowStepSchema] | None = None


# ── Synthesis ─────────────────────────────────────────────────────────────────

class SynthesisRequest(BaseModel):
    target: SynthesisTarget = SynthesisTarget.PYTHON
    deployment_mode: str = "stdio"
    permission_scopes: list[str] = Field(default_factory=list)
    auth_injection: str = "env_var"


class SynthesisResponse(BaseModel):
    job_id: UUID
    artifact_url: str | None = None
    message: str

"""Stage-to-stage data contracts. Every field is typed. No Any except JSONSchema blobs."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Taxonomy ──────────────────────────────────────────────────────────────────

ENTITY_TAXONOMY: list[str] = [
    "User", "Account", "Profile", "Session",
    "Customer", "Order", "Cart", "Product", "SKU",
    "Invoice", "Payment", "Transaction", "Subscription",
    "Document", "File", "Media", "Asset", "Template",
    "Message", "Notification", "Email", "Comment",
    "Team", "Organization", "Department", "Role",
    "Integration", "Webhook", "APIKey", "Config",
    "Report", "Metric", "Event", "Log",
]

ACTION_TAXONOMY: list[str] = [
    "Create", "Read", "Update", "Delete", "List", "Search",
    "Export", "Import", "Approve", "Reject", "Submit", "Cancel",
    "Enable", "Disable", "Activate", "Deactivate", "Assign",
    "Notify", "Validate", "Calculate",
]

HTTP_METHOD_DEFAULT_ACTION: dict[str, str] = {
    "GET": "Read",
    "POST": "Create",
    "PUT": "Update",
    "PATCH": "Update",
    "DELETE": "Delete",
}

DOMAIN_VERB_OVERRIDES: dict[str, str] = {
    "activate": "Activate", "deactivate": "Deactivate",
    "enable": "Enable", "disable": "Disable",
    "approve": "Approve", "reject": "Reject",
    "submit": "Submit", "cancel": "Cancel",
    "assign": "Assign", "notify": "Notify",
    "validate": "Validate", "calculate": "Calculate",
    "search": "Search", "export": "Export",
    "import": "Import",
}


# ── Stage 1 — Discovery output ────────────────────────────────────────────────

class ParameterSchema(BaseModel):
    name: str
    location: str = Field(alias="in", default="query")
    required: bool = False
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    description: str | None = None

    model_config = {"populate_by_name": True}


class RawEndpoint(BaseModel):
    id: str
    method: str
    path: str
    operation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    parameters: list[ParameterSchema] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    security: list[str] = Field(default_factory=list)

    @field_validator("method")
    @classmethod
    def uppercase_method(cls, v: str) -> str:
        return v.upper()


class RawApplicationModel(BaseModel):
    app_id: str
    endpoints: list[RawEndpoint]
    security_schemes: dict[str, Any] = Field(default_factory=dict)
    servers: list[str] = Field(default_factory=list)
    source_format: str

    @property
    def endpoint_count(self) -> int:
        return len(self.endpoints)


# ── Stage 2 — Extractor output ────────────────────────────────────────────────

class Relationship(BaseModel):
    owner: str
    owned: str
    type: str = "OWNS"
    cardinality: str = "one-to-many"


class AnnotatedEndpoint(BaseModel):
    id: str
    method: str
    path: str
    operation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    parameters: list[ParameterSchema] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    security: list[str] = Field(default_factory=list)
    # Extracted fields
    entity: str
    action: str
    parent_entity: str | None = None
    extraction_confidence: float = 1.0
    inferred_relationships: list[Relationship] = Field(default_factory=list)


class AnnotatedApplicationModel(BaseModel):
    app_id: str
    endpoints: list[AnnotatedEndpoint]
    relationships: list[Relationship] = Field(default_factory=list)


# ── Stage 5 — Compression contracts ──────────────────────────────────────────

class ClusterAssignment(BaseModel):
    operation_id: str
    cluster_id: int
    is_noise: bool


class UnifiedSchema(BaseModel):
    required_fields: list[dict[str, Any]] = Field(default_factory=list)
    optional_fields: list[dict[str, Any]] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)

    def to_json_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for f in self.required_fields:
            properties[f["name"]] = f.get("schema", {"type": "string"})
            required.append(f["name"])
        for f in self.optional_fields:
            properties[f["name"]] = f.get("schema", {"type": "string"})
        return {"type": "object", "properties": properties, "required": required}


class ToolDefinition(BaseModel):
    id: str
    app_id: str
    name: str
    description: str
    entity: str
    action: str
    parent_entity: str | None = None
    unified_schema: dict[str, Any]
    operation_ids: list[str]
    is_workflow: bool = False
    workflow_id: str | None = None
    permission_scope: list[str] = Field(default_factory=list)
    confidence: float


# ── Stage 6 — Workflow contracts ──────────────────────────────────────────────

class WorkflowStep(BaseModel):
    operation_id: str
    step_index: int
    required: bool = True


class WorkflowDefinition(BaseModel):
    id: str
    app_id: str
    name: str
    description: str
    steps: list[WorkflowStep]
    confidence: float
    detection_signals: list[str] = Field(default_factory=list)


# ── LLM response models (validated before touching graph) ─────────────────────

class EntityExtractionResult(BaseModel):
    entity: str
    confidence: float = Field(ge=0.0, le=1.0)


class EntityActionLabelResult(BaseModel):
    canonical_name: str
    entity: str
    action: str
    parent_entity: str | None = None
    description: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("entity")
    @classmethod
    def validate_entity(cls, v: str) -> str:
        all_entities = ENTITY_TAXONOMY + [e.lower() for e in ENTITY_TAXONOMY]
        if v not in ENTITY_TAXONOMY:
            # Accept case-insensitive match, normalize to PascalCase
            for e in ENTITY_TAXONOMY:
                if e.lower() == v.lower():
                    return e
            # Unknown entity — accept it (dynamic domain entities are valid)
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ACTION_TAXONOMY:
            for a in ACTION_TAXONOMY:
                if a.lower() == v.lower():
                    return a
            raise ValueError(f"Action '{v}' not in taxonomy: {ACTION_TAXONOMY}")
        return v


class DescriptionResult(BaseModel):
    description: str


class NormalizationResult(BaseModel):
    canonical_name: str
    confidence: float = Field(ge=0.0, le=1.0)


class WorkflowHypothesisItem(BaseModel):
    name: str
    description: str
    steps: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class WorkflowHypothesisResult(BaseModel):
    workflows: list[WorkflowHypothesisItem]


# ── Pipeline config (ablation flags) ─────────────────────────────────────────

class SynthesisTarget(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    BOTH = "both"


class PipelineConfig(BaseModel):
    synthesis_target: SynthesisTarget = SynthesisTarget.PYTHON
    enable_compression: bool = True
    enable_workflow_discovery: bool = True
    permission_scopes: list[str] = Field(default_factory=list)
    experiment_id: str | None = None
    resume_from_stage: str | None = None

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import StageError, SynthesisError
from app.db.engine import get_session_factory
from app.db.models import Application, Job
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.pipeline.base import BaseStage
from app.schemas.pipeline import SynthesisTarget
from app.storage.minio import get_minio_client

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


class SynthesisEngine(BaseStage):
    """Generates executable MCP server code from the enriched Capability Graph."""

    stage_name = "synthesizer"

    def __init__(self) -> None:
        self._neo4j = get_neo4j_client()
        self._queries = GraphQueries(self._neo4j)
        self._minio = get_minio_client()
        self._jinja = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["py", "ts"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    async def execute(self, job_id: str) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if not job:
                raise StageError(self.stage_name, f"Job {job_id} not found")

            app_result = await session.execute(
                select(Application).where(Application.id == job.app_id)
            )
            app = app_result.scalar_one_or_none()
            if not app:
                raise StageError(self.stage_name, f"Application not found for job {job_id}")

            config = job.pipeline_config or {}
            target = SynthesisTarget(config.get("synthesis_target", "python"))
            permission_scopes = config.get("permission_scopes", [])
            app_id = str(job.app_id)
            base_url = app.base_url or ""
            app_name = app.name

        # Fetch tools and workflows
        tools_raw = await self._queries.get_tools(app_id)
        workflows_raw = await self._queries.get_workflows(app_id)

        tools = self._build_tool_context(tools_raw, permission_scopes)
        workflows = self._build_workflow_context(workflows_raw)

        if not tools:
            raise SynthesisError("No tools available for synthesis — run compression first")

        # Generate files
        artifacts: dict[str, str] = {}

        if target in (SynthesisTarget.PYTHON, SynthesisTarget.BOTH):
            py_code = self._render_python(app_name, base_url, tools, workflows)
            artifacts["server.py"] = py_code

        if target in (SynthesisTarget.TYPESCRIPT, SynthesisTarget.BOTH):
            ts_code = self._render_typescript(app_name, base_url, tools, workflows)
            artifacts["server.ts"] = ts_code

        # Package as JSON bundle and store in MinIO
        artifact_key = f"artifacts/{app_id}/{job_id}/mcp_server.json"
        payload = json.dumps(
            {
                "app_id": app_id,
                "app_name": app_name,
                "tool_count": len(tools),
                "workflow_count": len(workflows),
                "files": artifacts,
            },
            indent=2,
        )

        await self._minio.put_object(
            bucket=self._minio.bucket_artifacts,
            key=artifact_key,
            data=payload.encode("utf-8"),
            content_type="application/json",
        )

        # Update job with artifact key
        async with get_session_factory()() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one()
            job.artifact_object_key = artifact_key
            job.status = "complete"
            await session.commit()

        return {
            "tools_synthesized": len(tools),
            "workflows_synthesized": len(workflows),
            "artifact_key": artifact_key,
            "targets": [target.value],
        }

    def _build_tool_context(
        self, tools_raw: list[dict[str, Any]], permission_scopes: list[str]
    ) -> list[dict[str, Any]]:
        tools = []
        for row in tools_raw:
            t = row.get("t") or {}
            required_scopes = t.get("permission_scope") or []
            # Permission-aware filtering
            if permission_scopes and required_scopes:
                if not all(s in permission_scopes for s in required_scopes):
                    continue

            try:
                schema = json.loads(t.get("unified_schema") or "{}")
            except Exception:
                schema = {}

            tools.append({
                "name": t.get("name", "unknown_tool"),
                "description": t.get("description", ""),
                "schema": schema,
                "is_workflow": t.get("is_workflow", False),
                "required_params": self._extract_required_params(schema),
                "optional_params": self._extract_optional_params(schema),
            })
        return tools

    def _build_workflow_context(self, workflows_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        workflows = []
        for row in workflows_raw:
            w = row.get("w") or {}
            steps = sorted(row.get("steps") or [], key=lambda s: s.get("step", 0))
            workflows.append({
                "name": w.get("name", "workflow"),
                "description": w.get("description", ""),
                "steps": [s.get("op_id") for s in steps if s.get("op_id")],
            })
        return workflows

    def _extract_required_params(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        return [
            {"name": k, "type": self._json_type_to_python(v.get("type", "str"))}
            for k, v in props.items()
            if k in required
        ]

    def _extract_optional_params(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        return [
            {"name": k, "type": self._json_type_to_python(v.get("type", "str"))}
            for k, v in props.items()
            if k not in required
        ]

    def _json_type_to_python(self, json_type: str) -> str:
        return {
            "string": "str", "integer": "int", "number": "float",
            "boolean": "bool", "array": "list", "object": "dict",
        }.get(json_type, "str")

    def _json_type_to_ts(self, json_type: str) -> str:
        return {
            "string": "string", "integer": "number", "number": "number",
            "boolean": "boolean", "array": "unknown[]", "object": "Record<string, unknown>",
        }.get(json_type, "string")

    def _render_python(
        self,
        app_name: str,
        base_url: str,
        tools: list[dict[str, Any]],
        workflows: list[dict[str, Any]],
    ) -> str:
        template = self._jinja.get_template("python_mcp/server.py.j2")
        return template.render(
            app_name=app_name,
            base_url=base_url,
            tools=tools,
            workflows=workflows,
        )

    def _render_typescript(
        self,
        app_name: str,
        base_url: str,
        tools: list[dict[str, Any]],
        workflows: list[dict[str, Any]],
    ) -> str:
        template = self._jinja.get_template("typescript_mcp/server.ts.j2")
        return template.render(
            app_name=app_name,
            base_url=base_url,
            tools=tools,
            workflows=workflows,
            json_type_to_ts=self._json_type_to_ts,
        )

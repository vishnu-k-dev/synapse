from __future__ import annotations

import json
import keyword
import re
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


def _schema_type(param: dict[str, Any]) -> str:
    """Best-effort JSON-schema type for a routing param entry."""
    schema = param.get("schema")
    if isinstance(schema, dict):
        t = schema.get("type")
        if isinstance(t, str):
            return t
    return "string"


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
        tools: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for row in tools_raw:
            t = row.get("t") or {}
            required_scopes = t.get("permission_scope") or []
            # Permission-aware filtering
            if permission_scopes and required_scopes:
                if not all(s in permission_scopes for s in required_scopes):
                    continue

            member_ops = [op for op in (row.get("operations") or []) if op and op.get("id")]
            primary = self._select_primary(member_ops)
            routing = self._parse_routing(primary)

            required_params, optional_params, query_args, body_args, url_path = (
                self._routing_to_params(routing)
            )

            tools.append({
                "name": self._unique_tool_name(t.get("name", "call_tool"), used_names),
                "description": (t.get("description") or "").replace("\n", " ").strip()
                or "Calls the underlying API operation.",
                "is_workflow": False,
                "method": (routing.get("method") or "GET").upper(),
                "path": url_path,
                "required_params": required_params,
                "optional_params": optional_params,
                "query_args": query_args,
                "body_args": body_args,
                "has_body": bool(body_args),
                "member_count": len(member_ops),
            })
        return tools

    def _select_primary(self, member_ops: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Pick the operation whose routing drives the generated call. Prefer a
        write op (richest params); fall back to the first member."""
        if not member_ops:
            return None
        for op in member_ops:
            if self._parse_routing(op).get("has_body"):
                return op
        return member_ops[0]

    def _parse_routing(self, op: dict[str, Any] | None) -> dict[str, Any]:
        if not op:
            return {}
        try:
            return json.loads(op.get("routing") or "{}")
        except Exception:
            return {}

    def _routing_to_params(
        self, routing: dict[str, Any]
    ) -> tuple[list[dict], list[dict], list[dict], list[dict], str]:
        """Turn a routing dict into signature params + request builders.

        Returns (required_params, optional_params, query_args, body_args, url_path).
        Path params are substituted into url_path using the sanitized arg name so
        the rendered f-string resolves against the function arguments.
        """
        seen: set[str] = set()

        def arg_of(name: str) -> str:
            a = re.sub(r"\W", "_", name) or "param"
            if not (a[0].isalpha() or a[0] == "_"):
                a = "p_" + a
            if keyword.iskeyword(a):
                a = a + "_"
            base, i = a, 2
            while a in seen:
                a, i = f"{base}{i}", i + 1
            seen.add(a)
            return a

        required: list[dict] = []
        optional: list[dict] = []
        query_args: list[dict] = []
        body_args: list[dict] = []
        url_path = routing.get("path") or "/"

        for p in routing.get("path_params", []):
            arg = arg_of(p["name"])
            url_path = url_path.replace("{" + p["name"] + "}", "{" + arg + "}")
            required.append({"arg": arg, "name": p["name"], "location": "path",
                             "type": self._json_type_to_python(_schema_type(p))})

        for p in routing.get("query_params", []):
            arg = arg_of(p["name"])
            entry = {"arg": arg, "name": p["name"], "location": "query",
                     "type": self._json_type_to_python(_schema_type(p))}
            (required if p.get("required") else optional).append(entry)
            query_args.append({"arg": arg, "name": p["name"]})

        for p in routing.get("body_params", []):
            arg = arg_of(p["name"])
            entry = {"arg": arg, "name": p["name"], "location": "body",
                     "type": self._json_type_to_python(_schema_type(p))}
            (required if p.get("required") else optional).append(entry)
            body_args.append({"arg": arg, "name": p["name"]})

        return required, optional, query_args, body_args, url_path

    def _unique_tool_name(self, raw: str, used: set[str]) -> str:
        name = re.sub(r"\W", "_", (raw or "call_tool").strip().lower()).strip("_") or "call_tool"
        if not (name[0].isalpha() or name[0] == "_"):
            name = "op_" + name
        if keyword.iskeyword(name):
            name = name + "_"
        base, i = name, 2
        while name in used:
            name, i = f"{base}_{i}", i + 1
        used.add(name)
        return name

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

from __future__ import annotations

import json
import uuid
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx
import jsonref
import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.security import ParseError, SecurityError, StageError
from app.db.engine import get_session_factory
from app.db.models import Job
from app.pipeline.base import BaseStage
from app.schemas.pipeline import ParameterSchema, RawApplicationModel, RawEndpoint
from app.storage.minio import get_minio_client

logger = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.")
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                  "metadata.google.internal", "169.254.169.254"}


def _assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme not in _ALLOWED_SCHEMES:
        raise SecurityError(f"Disallowed scheme '{scheme}' in $ref URL: {url}")
    if host in _BLOCKED_HOSTS:
        raise SecurityError(f"Blocked host '{host}' in $ref URL: {url}")
    for prefix in _PRIVATE_PREFIXES:
        if host.startswith(prefix):
            raise SecurityError(f"Private IP range blocked in $ref URL: {url}")
    try:
        ip = ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise SecurityError(f"Private/loopback IP blocked: {url}")
    except ValueError:
        pass  # hostname — not an IP, fine


def _safe_remote_loader(uri: str) -> dict[str, Any]:
    _assert_safe_url(uri)
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        response = client.get(uri)
        response.raise_for_status()
        ct = response.headers.get("content-type", "")
        if "yaml" in ct or uri.endswith((".yaml", ".yml")):
            return yaml.safe_load(response.text)
        return response.json()


class OpenAPIParser:
    """Parses OpenAPI 3.x (JSON or YAML) into a list of RawEndpoints."""

    MAX_SPEC_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

    def parse_text(self, content: str, app_id: str) -> RawApplicationModel:
        raw = self._load_raw(content)
        self._validate_spec(raw)
        resolved = jsonref.replace_refs(raw, loader=_safe_remote_loader)
        return self._extract(resolved, app_id)

    def _load_raw(self, content: str) -> dict[str, Any]:
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ParseError(f"Could not parse spec as JSON or YAML: {exc}") from exc

    def _validate_spec(self, raw: dict[str, Any]) -> None:
        try:
            validate(raw)
        except Exception as exc:
            raise ParseError(f"OpenAPI spec validation failed: {exc}") from exc

    def _extract(self, spec: dict[str, Any], app_id: str) -> RawApplicationModel:
        endpoints: list[RawEndpoint] = []
        paths = spec.get("paths") or {}
        security_schemes = (spec.get("components") or {}).get("securitySchemes") or {}
        servers = [s.get("url", "") for s in (spec.get("servers") or [])]

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            path_level_params = path_item.get("parameters") or []

            for method in ("get", "post", "put", "patch", "delete"):
                op = path_item.get(method)
                if not isinstance(op, dict):
                    continue

                all_params = list(path_level_params) + (op.get("parameters") or [])
                request_schema = self._extract_request_schema(op)
                response_schema = self._extract_response_schema(op)
                security = self._extract_security(op, spec)

                endpoint = RawEndpoint(
                    id=str(uuid.uuid4()),
                    method=method.upper(),
                    path=path,
                    operation_id=op.get("operationId"),
                    tags=op.get("tags") or [],
                    description=(op.get("description") or op.get("summary") or ""),
                    parameters=[self._parse_param(p) for p in all_params if isinstance(p, dict)],
                    request_schema=request_schema,
                    response_schema=response_schema,
                    security=security,
                )
                endpoints.append(endpoint)

        return RawApplicationModel(
            app_id=app_id,
            endpoints=endpoints,
            security_schemes=dict(security_schemes),
            servers=servers,
            source_format="openapi3",
        )

    def _extract_request_schema(self, op: dict[str, Any]) -> dict[str, Any] | None:
        rb = op.get("requestBody")
        if not rb:
            return None
        content = rb.get("content") or {}
        for media_type in ("application/json", "application/x-www-form-urlencoded"):
            if media_type in content:
                return content[media_type].get("schema")
        return None

    def _extract_response_schema(self, op: dict[str, Any]) -> dict[str, Any] | None:
        responses = op.get("responses") or {}
        for code in ("200", "201", "202"):
            resp = responses.get(code)
            if not resp:
                continue
            content = resp.get("content") or {}
            if "application/json" in content:
                return content["application/json"].get("schema")
        return None

    def _extract_security(self, op: dict[str, Any], spec: dict[str, Any]) -> list[str]:
        security = op.get("security") or spec.get("security") or []
        return [list(s.keys())[0] for s in security if s]

    def _parse_param(self, p: dict[str, Any]) -> ParameterSchema:
        return ParameterSchema(
            name=p.get("name", ""),
            **{"in": p.get("in", "query")},
            required=p.get("required", False),
            schema=p.get("schema"),
            description=p.get("description"),
        )


class PostmanParser:
    """Parses Postman Collection v2.1 into a list of RawEndpoints."""

    def parse_text(self, content: str, app_id: str) -> RawApplicationModel:
        try:
            collection = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid Postman JSON: {exc}") from exc

        if "collection" in collection:
            collection = collection["collection"]

        endpoints: list[RawEndpoint] = []
        self._walk_items(collection.get("item") or [], endpoints)

        return RawApplicationModel(
            app_id=app_id,
            endpoints=endpoints,
            security_schemes={},
            servers=[],
            source_format="postman21",
        )

    def _walk_items(self, items: list[dict[str, Any]], out: list[RawEndpoint]) -> None:
        for item in items:
            if "item" in item:
                self._walk_items(item["item"], out)
                continue
            req = item.get("request")
            if not isinstance(req, dict):
                continue
            endpoint = self._parse_request(item.get("name", ""), req)
            if endpoint:
                out.append(endpoint)

    def _parse_request(self, name: str, req: dict[str, Any]) -> RawEndpoint | None:
        method = (req.get("method") or "GET").upper()
        url = req.get("url") or {}
        if isinstance(url, str):
            path = "/" + "/".join(url.split("/")[3:])
        else:
            raw_path = "/" + "/".join(url.get("path") or [])
            path = raw_path.replace(":{{", "{").replace("}}", "}")

        query_params = [
            ParameterSchema(name=q.get("key", ""), **{"in": "query"})
            for q in (url.get("query") or []) if q.get("key")
        ] if isinstance(url, dict) else []

        description = (req.get("description") or name or "")
        if isinstance(description, dict):
            description = description.get("content", name)

        return RawEndpoint(
            id=str(uuid.uuid4()),
            method=method,
            path=path,
            description=str(description)[:1000],
            parameters=query_params,
        )


class DiscoveryEngine(BaseStage):
    stage_name = "discovery"

    def __init__(self) -> None:
        self._openapi = OpenAPIParser()
        self._postman = PostmanParser()

    async def execute(self, job_id: str) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if not job:
                raise StageError(self.stage_name, f"Job {job_id} not found")

            app_id = str(job.app_id)

        # Fetch spec from MinIO
        minio = get_minio_client()
        from app.db.engine import get_session_factory as gsf
        async with get_session_factory()() as session:
            from sqlalchemy import select
            from app.db.models import Application
            result = await session.execute(
                select(Application).where(Application.id == uuid.UUID(app_id))
            )
            app = result.scalar_one_or_none()
            if not app or not app.spec_object_key:
                raise StageError(self.stage_name, f"No spec object key for app {app_id}")

            spec_content = await minio.get_object(
                bucket=minio.bucket_specs, key=app.spec_object_key
            )
            source_format = app.source_format or "openapi3"

        if source_format == "postman21":
            model = self._postman.parse_text(spec_content, app_id)
        else:
            model = self._openapi.parse_text(spec_content, app_id)

        # Persist raw model to job
        async with get_session_factory()() as session:
            result = await session.execute(
                select(Job).where(Job.id == uuid.UUID(job_id))
            )
            job = result.scalar_one()
            job.raw_model = model.model_dump()
            await session.commit()

        return {
            "endpoint_count": model.endpoint_count,
            "source_format": model.source_format,
            "servers": model.servers,
        }

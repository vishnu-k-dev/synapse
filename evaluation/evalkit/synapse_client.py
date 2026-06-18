"""HTTP client that drives the SYNAPSE backend through one ablation condition.

Mirrors the proven create -> upload -> poll -> fetch dance in ``scripts/e2e_test.py``
but parameterizes the ``pipeline_config`` (so we can sweep the 2x2 ablation) and the
app ``base_url`` (so the generated server's hardcoded ``BASE_URL`` points at our
sandbox). Imports nothing from ``backend/`` — pure HTTP, honoring the standalone invariant.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from evalkit.conditions import Condition

_TERMINAL = {"complete", "failed", "cancelled"}


@dataclass
class SynthesisResult:
    app_id: str
    job_id: str
    status: str
    condition_key: str
    tool_count: int
    files: dict[str, str]          # filename -> source code (e.g. "server.py")
    raw_artifact: dict[str, Any]
    events: list[dict[str, Any]]

    @property
    def server_py(self) -> str:
        if "server.py" not in self.files:
            raise KeyError(f"artifact has no server.py (files: {list(self.files)})")
        return self.files["server.py"]


class SynapseClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SynapseClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── health ────────────────────────────────────────────────────────────────
    def healthy(self) -> bool:
        try:
            return self._http.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    # ── pipeline steps ─────────────────────────────────────────────────────────
    def create_app(self, name: str, sandbox_base_url: str, condition: Condition,
                    synthesis_target: str = "python") -> tuple[str, str]:
        payload = {
            "name": name,
            "description": f"eval/{condition.key}",
            "base_url": sandbox_base_url,
            "pipeline_config": condition.pipeline_config(synthesis_target),
        }
        r = self._http.post("/api/v1/apps", json=payload)
        r.raise_for_status()
        data = r.json()
        return data["app_id"], data["job_id"]

    def upload_spec(self, app_id: str, spec_path: str, source_format: str = "openapi3") -> str:
        spec = Path(spec_path)
        content_type = "application/x-yaml" if spec.suffix in (".yaml", ".yml") else "application/json"
        r = self._http.post(
            f"/api/v1/apps/{app_id}/spec",
            files={"file": (spec.name, spec.read_bytes(), content_type)},
        )
        r.raise_for_status()
        return r.json().get("job_id", "")

    def get_job(self, job_id: str) -> dict[str, Any]:
        r = self._http.get(f"/api/v1/jobs/{job_id}")
        r.raise_for_status()
        return r.json()

    def poll_job(self, job_id: str, timeout_s: int, interval_s: float = 2.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            data = self.get_job(job_id)
            if data.get("status") in _TERMINAL:
                return data
            # Fail fast: the backend leaves job.status='running' when a stage fails
            # (it only flips to 'complete' at the synthesizer), so without this a
            # stage failure would hang until timeout. Surface the real stage error now.
            for ev in data.get("events", []):
                if ev.get("status") == "failed":
                    raise RuntimeError(
                        f"pipeline stage '{ev.get('stage')}' failed: {ev.get('error_message')}"
                    )
            time.sleep(interval_s)
        raise TimeoutError(f"job {job_id} did not finish within {timeout_s}s")

    def get_tools(self, app_id: str) -> list[dict[str, Any]]:
        r = self._http.get(f"/api/v1/tools/{app_id}")
        r.raise_for_status()
        return r.json()

    def get_graph(self, app_id: str) -> dict[str, Any]:
        r = self._http.get(f"/api/v1/graph/{app_id}")
        r.raise_for_status()
        return r.json()

    def fetch_artifact(self, job_data: dict[str, Any]) -> dict[str, Any]:
        url = job_data.get("artifact_url")
        if not url:
            raise RuntimeError("job has no artifact_url — synthesizer did not complete")
        # Presigned MinIO URL — no auth header.
        r = httpx.get(url, timeout=30.0)
        r.raise_for_status()
        return r.json()

    # ── high-level orchestration ───────────────────────────────────────────────
    def synthesize(self, *, api_name: str, spec_path: str, sandbox_base_url: str,
                   condition: Condition, source_format: str = "openapi3",
                   timeout_s: int = 600) -> SynthesisResult:
        """Run one full condition end-to-end and return the generated artifact."""
        app_id, job_id = self.create_app(api_name, sandbox_base_url, condition)
        upload_job = self.upload_spec(app_id, spec_path, source_format)
        job_data = self.poll_job(upload_job or job_id, timeout_s)

        if job_data.get("status") != "complete":
            raise RuntimeError(
                f"[{condition.key}] pipeline ended '{job_data.get('status')}': "
                f"{job_data.get('error_message')}"
            )

        artifact = self.fetch_artifact(job_data)
        files = artifact.get("files") or {}
        return SynthesisResult(
            app_id=app_id,
            job_id=job_data["job_id"],
            status=job_data["status"],
            condition_key=condition.key,
            tool_count=int(artifact.get("tool_count", len(files))),
            files=files,
            raw_artifact=artifact,
            events=job_data.get("events", []),
        )

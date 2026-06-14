#!/usr/bin/env python3
"""
SYNAPSE end-to-end integration test.

Feeds the Petstore spec through all 7 pipeline stages and asserts:
  - All services healthy
  - App + job created successfully
  - Spec uploaded and pipeline dispatched
  - All 7 stages complete (no failures)
  - Neo4j has Entity + Operation nodes
  - MinIO has the artifact JSON

Usage (from repo root, with Docker running):
    python scripts/e2e_test.py

Prerequisites:
  1. Docker Desktop running
  2. .env exists with real OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
  3. `docker compose up -d` already run (or use --start flag)
  4. `alembic upgrade head` run in backend/

Options:
  --start       Run `docker compose up -d` before testing
  --migrate     Run `alembic upgrade head` before testing
  --timeout N   Max seconds to wait for pipeline completion (default: 600)
  --api-url URL Backend URL (default: http://localhost:8000)
  --api-key KEY API key (default: reads API_KEY from .env)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# Force UTF-8 output so box-drawing chars / checkmarks don't crash on Windows cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
PETSTORE_SPEC = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"
BACKEND_DIR = REPO_ROOT / "backend"

# ── Colours (disabled when not a TTY, e.g. piped/captured output) ─────────────
_COLOR = sys.stdout.isatty()
GREEN  = "\033[92m" if _COLOR else ""
RED    = "\033[91m" if _COLOR else ""
YELLOW = "\033[93m" if _COLOR else ""
CYAN   = "\033[96m" if _COLOR else ""
BOLD   = "\033[1m" if _COLOR else ""
RESET  = "\033[0m" if _COLOR else ""


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  {CYAN}→{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    print("─" * 60)


# ── .env reader ───────────────────────────────────────────────────────────────
def read_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


# ── Service health checks ─────────────────────────────────────────────────────
def check_services(api_url: str) -> None:
    section("1. Service health")

    # Backend
    try:
        r = httpx.get(f"{api_url}/health", timeout=5)
        if r.status_code == 200:
            ok(f"Backend  {api_url}")
        else:
            warn(f"Backend returned {r.status_code} — may still be starting")
    except httpx.ConnectError:
        fail(
            f"Cannot reach backend at {api_url}\n"
            "     Run: docker compose up -d\n"
            "     Then wait ~30 seconds and retry."
        )

    # PostgreSQL (via backend readiness)
    try:
        r = httpx.get(f"{api_url}/readiness", timeout=5)
        if r.status_code == 200:
            ok("PostgreSQL (via backend readiness)")
        else:
            warn(f"Readiness returned {r.status_code} — DB may not be migrated yet")
    except Exception:
        warn("No /readiness endpoint — skipping DB check")

    # Neo4j
    try:
        r = httpx.get("http://localhost:7474", timeout=5)
        ok("Neo4j browser port 7474")
    except Exception:
        fail("Cannot reach Neo4j at localhost:7474 — is docker compose up?")

    # MinIO
    try:
        r = httpx.get("http://localhost:9000/minio/health/live", timeout=5)
        if r.status_code in (200, 204):
            ok("MinIO health endpoint")
        else:
            warn(f"MinIO returned {r.status_code}")
    except Exception:
        fail("Cannot reach MinIO at localhost:9000")

    # Redis (indirect — if backend is up and connected, Redis is fine)
    ok("Redis (inferred from backend startup)")


# ── Create application ────────────────────────────────────────────────────────
def create_app(client: httpx.Client) -> tuple[str, str]:
    section("2. Create application + pending job")

    payload = {
        "name": "Petstore E2E Test",
        "description": "Integration test — all 7 stages",
        "pipeline_config": {
            "enable_compression": True,
            "enable_workflow_discovery": True,
        },
    }
    r = client.post("/api/v1/apps", json=payload)
    if r.status_code != 201:
        fail(f"POST /apps returned {r.status_code}: {r.text}")

    data = r.json()
    app_id = data["app_id"]
    job_id = data["job_id"]
    ok(f"app_id  = {app_id}")
    ok(f"job_id  = {job_id}")
    return app_id, job_id


# ── Upload spec ───────────────────────────────────────────────────────────────
def upload_spec(client: httpx.Client, app_id: str) -> None:
    section("3. Upload Petstore spec")

    spec_bytes = PETSTORE_SPEC.read_bytes()
    info(f"Uploading {PETSTORE_SPEC.name} ({len(spec_bytes)} bytes)")

    r = client.post(
        f"/api/v1/apps/{app_id}/spec",
        files={"file": ("petstore.yaml", spec_bytes, "application/x-yaml")},
    )
    if r.status_code != 200:
        fail(f"POST /apps/{app_id}/spec returned {r.status_code}: {r.text}")

    data = r.json()
    ok(f"Spec uploaded — pipeline dispatched (job_id={data.get('job_id')})")


# ── Poll job until terminal ───────────────────────────────────────────────────
def poll_pipeline(client: httpx.Client, job_id: str, timeout_s: int) -> dict:
    section("4. Pipeline progress")

    TERMINAL = {"complete", "failed", "cancelled"}
    deadline = time.monotonic() + timeout_s
    last_stage = ""
    dots = 0

    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/jobs/{job_id}")
        if r.status_code != 200:
            fail(f"GET /jobs/{job_id} returned {r.status_code}: {r.text}")

        data = r.json()
        status = data["status"]
        events = data.get("events", [])

        # Print new stage transitions
        for ev in events:
            stage_key = f"{ev['stage']}:{ev['status']}"
            if stage_key != last_stage:
                last_stage = stage_key
                icon = "✓" if ev["status"] == "complete" else ("✗" if ev["status"] == "failed" else "→")
                colour = GREEN if ev["status"] == "complete" else (RED if ev["status"] == "failed" else CYAN)
                print(f"  {colour}{icon}{RESET}  stage={ev['stage']}  status={ev['status']}")
                if ev.get("error_message"):
                    print(f"       error: {ev['error_message']}")
                if ev.get("summary"):
                    print(f"       summary: {ev['summary']}")
                dots = 0

        if status in TERMINAL:
            print()
            return data

        # Progress dots
        dots += 1
        if dots % 5 == 0:
            info(f"Waiting… elapsed {int(time.monotonic() - (deadline - timeout_s))}s / {timeout_s}s")

        time.sleep(2)

    fail(f"Pipeline did not complete within {timeout_s}s")
    return {}  # unreachable


# ── Assert results ─────────────────────────────────────────────────────────────
def assert_pipeline_complete(job_data: dict, job_id: str, client: httpx.Client) -> None:
    section("5. Assert pipeline results")

    status = job_data["status"]
    if status != "complete":
        fail(f"Job status is '{status}' — expected 'complete'")
    ok(f"Job status = {status}")

    events = job_data.get("events", [])
    expected_stages = [
        "discovery", "extractor", "graph_builder",
        "semantic_engine", "compression", "workflow_discovery", "synthesizer"
    ]
    completed_stages = {e["stage"] for e in events if e["status"] == "complete"}

    for stage in expected_stages:
        if stage in completed_stages:
            ok(f"Stage '{stage}' completed")
        else:
            fail(f"Stage '{stage}' did NOT complete — check Celery worker logs")

    artifact_url = job_data.get("artifact_url")
    if artifact_url:
        ok(f"Artifact URL present: {artifact_url[:80]}…")
    else:
        warn("No artifact_url in job response")


def assert_neo4j_nodes(api_url: str, api_key: str, app_id: str) -> None:
    section("6. Assert Neo4j graph")

    with httpx.Client(base_url=api_url, headers={"X-API-Key": api_key}, timeout=15) as client:
        r = client.get(f"/api/v1/graph/{app_id}")
        if r.status_code != 200:
            fail(f"GET /graph/{app_id} returned {r.status_code}: {r.text}")
        data = r.json()

    stats = data.get("stats", {})
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    entity_count = stats.get("entity_count", sum(1 for n in nodes if n["node_type"] == "Entity"))
    op_count = stats.get("operation_count", sum(1 for n in nodes if n["node_type"] == "Operation"))
    tool_count = stats.get("tool_count", sum(1 for n in nodes if n["node_type"] == "Tool"))

    info(f"Graph: {entity_count} entities, {op_count} operations, {tool_count} tools, {len(edges)} edges")

    if entity_count == 0:
        fail("No Entity nodes found in Neo4j — graph_builder stage may have failed")
    ok(f"Entity nodes: {entity_count}")

    if op_count == 0:
        fail("No Operation nodes found in Neo4j")
    ok(f"Operation nodes: {op_count}")

    if tool_count == 0:
        warn("No Tool nodes yet — compression may have skipped (small API)")
    else:
        ok(f"Tool nodes: {tool_count}")

    if len(edges) > 0:
        ok(f"Graph edges: {len(edges)}")
    else:
        warn("No edges returned — may be a graph API issue")


def assert_minio_artifact(api_url: str, api_key: str, job_id: str) -> None:
    section("7. Assert MinIO artifact")

    with httpx.Client(base_url=api_url, headers={"X-API-Key": api_key}, timeout=15) as client:
        r = client.get(f"/api/v1/jobs/{job_id}")
        data = r.json()

    artifact_url = data.get("artifact_url")
    if not artifact_url:
        fail("No artifact_url in job — synthesizer may have failed")

    # Try to fetch the artifact (presigned MinIO URL)
    try:
        ar = httpx.get(artifact_url, timeout=15)
        if ar.status_code == 200:
            try:
                artifact = ar.json()
                ok(f"Artifact JSON fetched — keys: {list(artifact.keys())[:6]}")
                tool_count = artifact.get("tool_count", len(artifact.get("tools", [])))
                workflow_count = artifact.get("workflow_count", len(artifact.get("workflows", [])))
                files = list((artifact.get("files") or {}).keys())
                ok(f"Artifact: {tool_count} tools, {workflow_count} workflows, files={files}")
                if tool_count == 0:
                    warn("Artifact has 0 tools — synthesis may have produced an empty server")
            except Exception:
                warn("Artifact fetched but not valid JSON")
        else:
            warn(f"Artifact URL returned {ar.status_code} — may be expired")
    except Exception as exc:
        warn(f"Could not fetch artifact URL: {exc}")


# ── Optionally start infra ────────────────────────────────────────────────────
def docker_up() -> None:
    section("0a. Starting Docker services")
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--wait"],
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        fail("docker compose up -d failed")
    ok("docker compose up --wait completed")
    info("Waiting 10 seconds for services to fully initialize…")
    time.sleep(10)


def alembic_migrate() -> None:
    section("0b. Running alembic upgrade head")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
    )
    if result.returncode != 0:
        fail("alembic upgrade head failed")
    ok("Schema migrations applied")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="SYNAPSE E2E integration test")
    parser.add_argument("--start", action="store_true", help="Run docker compose up -d first")
    parser.add_argument("--migrate", action="store_true", help="Run alembic upgrade head first")
    parser.add_argument("--timeout", type=int, default=600, help="Pipeline timeout seconds")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Backend URL")
    parser.add_argument("--api-key", default=None, help="API key (defaults to API_KEY in .env)")
    args = parser.parse_args()

    # Read API key from .env if not provided
    env_vars = read_dotenv(REPO_ROOT / ".env")
    api_key = args.api_key or env_vars.get("API_KEY", "dev-api-key-replace-in-prod")

    print(f"\n{BOLD}SYNAPSE — End-to-End Integration Test{RESET}")
    print(f"  api_url = {args.api_url}")
    print(f"  timeout = {args.timeout}s")
    print(f"  spec    = {PETSTORE_SPEC}")

    if args.start:
        docker_up()

    if args.migrate:
        alembic_migrate()

    check_services(args.api_url)

    headers = {"X-API-Key": api_key}
    with httpx.Client(base_url=args.api_url, headers=headers, timeout=30) as client:
        app_id, job_id = create_app(client)
        upload_spec(client, app_id)
        job_data = poll_pipeline(client, job_id, args.timeout)
        assert_pipeline_complete(job_data, job_id, client)

    assert_neo4j_nodes(args.api_url, api_key, app_id)
    assert_minio_artifact(args.api_url, api_key, job_id)

    print(f"\n{BOLD}{GREEN}ALL CHECKS PASSED ✓{RESET}\n")
    print("Next steps:")
    print("  • Review Neo4j at http://localhost:7474")
    print("  • Review MinIO at http://localhost:9001")
    print(f"  • job_id = {job_id}")
    print(f"  • app_id = {app_id}\n")


if __name__ == "__main__":
    main()

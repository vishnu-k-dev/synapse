"""Regression test for the full agent loop (keyless).

Runs the agent-loop de-risk as a subprocess (isolating the FastMCP server subprocess +
uvicorn thread + asyncio MCP client from the pytest event loop) and asserts it solves the
cross-entity workflow task end-to-end. No backend, no API keys.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = EVAL_ROOT / "scripts" / "derisk_agent_loop.py"


def test_agent_loop_workflow_end_to_end():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=180,
    )
    combined = proc.stdout + "\n" + proc.stderr
    assert "ALL CHECKS PASSED" in proc.stdout, combined
    assert proc.returncode == 0, combined

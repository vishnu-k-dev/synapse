"""Regression test for the full MCP execution spine.

Runs the keyless de-risk script as a subprocess (which isolates the FastMCP server
subprocess + uvicorn thread + asyncio MCP client from the pytest event loop) and
asserts it proves the whole spine end-to-end. No backend, no API keys required.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = EVAL_ROOT / "scripts" / "derisk_mcp_spine.py"


def test_mcp_execution_spine_end_to_end():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=180,
    )
    combined = proc.stdout + "\n" + proc.stderr
    assert "ALL CHECKS PASSED" in proc.stdout, combined
    assert proc.returncode == 0, combined

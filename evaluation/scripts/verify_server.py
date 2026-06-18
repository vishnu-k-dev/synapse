#!/usr/bin/env python3
"""Phase 6 verification — keyless. Run the synthesis-correctness check on a generated server.

Renders the real template with the Petstore tool fixture, launches it, and verifies every
tool round-trips against the sandbox. This is the regression guard for finding F-1: if a
list tool ever stops returning correctly, its check fails here.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from evalkit.fixtures import PETSTORE_APP_NAME, PETSTORE_NAIVE_TOOLS
from evalkit.mcp_bridge import McpToolClient
from evalkit.render import render_python_server
from evalkit.sandbox import SandboxServer
from evalkit.server_host import GeneratedServer
from evalkit.verify import verify

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"


async def _run(sandbox: SandboxServer):
    source = render_python_server(PETSTORE_APP_NAME, sandbox.base_url, PETSTORE_NAIVE_TOOLS)
    with GeneratedServer(source) as gs:
        async with McpToolClient(gs) as mcp:
            specs = await mcp.list_tools()
            return await verify(specs, mcp, sandbox, api="petstore")


def main() -> int:
    print("\nGenerated-server verification (Phase 6, keyless)\n" + "-" * 52)
    with SandboxServer(str(SPEC), seed=42) as sandbox:
        report = asyncio.run(_run(sandbox))

    for c in report.checks:
        mark = "PASS" if c.ok else "FAIL"
        print(f"  [{mark}] {c.name:<16} {c.kind:<13} {c.detail}")
    print("-" * 52)
    print(f"synthesis-correctness score: {report.passed}/{report.total} = {report.score:.0%}")

    all_ok = report.total > 0 and report.passed == report.total
    print("ALL TOOLS VERIFIED" if all_ok else "VERIFICATION FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

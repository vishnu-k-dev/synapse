#!/usr/bin/env python3
"""De-risk the full agent loop — keyless, using a deterministic MockChat.

Proves runner -> MCP -> generated server -> sandbox -> judge end-to-end on the hardest
task tier (a cross-entity workflow needing id-chaining), without any API key. The mock
"policy" reads the id returned by create_owner and feeds it into create_pet, exactly as a
real agent must — so the relational success oracle (where_ref) is genuinely exercised.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from evalkit.agent.judge import Judge
from evalkit.agent.llm import AssistantTurn, MockChat, ToolCall
from evalkit.agent.runner import AgentRunner
from evalkit.fixtures import PETSTORE_APP_NAME, PETSTORE_NAIVE_TOOLS
from evalkit.mcp_bridge import McpToolClient
from evalkit.render import render_python_server
from evalkit.sandbox import SandboxServer
from evalkit.server_host import GeneratedServer
from evalkit.tasks.schema import load_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parents[1] / "evalkit" / "tasks" / "suites" / "petstore.yaml"
SPEC = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"


def policy(messages, tools) -> AssistantTurn:
    """Solve 'owner_with_pet_carol': create Carol, then create Max owned by Carol."""
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if len(tool_msgs) == 0:
        return AssistantTurn(tool_calls=[
            ToolCall("c1", "create_owner", {"name": "Carol", "email": "carol@example.com"})])
    if len(tool_msgs) == 1:
        owner = json.loads(tool_msgs[0]["content"])
        return AssistantTurn(tool_calls=[
            ToolCall("c2", "create_pet",
                     {"name": "Max", "species": "dog", "owner_id": owner["id"]})])
    return AssistantTurn(content="Done — created Carol and her dog Max.")


async def _drive(sandbox: SandboxServer):
    source = render_python_server(PETSTORE_APP_NAME, sandbox.base_url, PETSTORE_NAIVE_TOOLS)
    task = {t.id: t for t in load_suite(str(SUITE), api="petstore")}["owner_with_pet_carol"]
    with GeneratedServer(source) as gs:
        async with McpToolClient(gs) as mcp:
            tool_specs = await mcp.list_tools()
            sandbox.reset()
            run = await AgentRunner(MockChat(policy), max_steps=8).run(
                task=task, tool_specs=tool_specs, mcp=mcp, condition_key="C1_naive", repeat=0)
            ok, reason = Judge(lambda: sandbox.store.snapshot()).judge(task)
            run.success, run.judge_reason = ok, reason
            return run


def main() -> int:
    print("\nAgent-loop de-risk (keyless, MockChat)\n" + "-" * 50)
    with SandboxServer(str(SPEC), seed=42) as sandbox:
        run = asyncio.run(_drive(sandbox))

    checks = [
        ("workflow task judged success", run.success, run.judge_reason),
        ("agent issued >=2 tool calls (real chaining)", run.tool_calls >= 2, str(run.tool_calls)),
        ("distinct tools used == 2", run.distinct_tools == 2, str(run.distinct_tools)),
        ("no tool selection errors", run.selection_errors == 0, str(run.selection_errors)),
        ("tool_count recorded", run.tool_count == len(PETSTORE_NAIVE_TOOLS), str(run.tool_count)),
    ]
    all_ok = True
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} -- {detail}")
        all_ok = all_ok and ok
    print("-" * 50)
    print("ALL CHECKS PASSED" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

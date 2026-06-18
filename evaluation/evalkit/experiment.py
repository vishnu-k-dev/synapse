"""Experiment orchestrator: api x condition x task x repeats -> results -> report.

A toolset provider yields the generated server.py source for a condition; the orchestrator
hosts it, opens one MCP session, and runs every task/repeat against a freshly-reset
sandbox. The agent acts through the MCP client; the judge scores against sandbox state.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from evalkit.agent.judge import Judge
from evalkit.agent.llm import ChatModel
from evalkit.agent.runner import AgentRunner
from evalkit.agent.transcript import RunResult
from evalkit.conditions import ALL_CONDITIONS, Condition
from evalkit.fixtures import PETSTORE_APP_NAME, PETSTORE_NAIVE_TOOLS
from evalkit.mcp_bridge import McpToolClient
from evalkit.render import render_python_server
from evalkit.sandbox import SandboxServer
from evalkit.server_host import GeneratedServer
from evalkit.store import write_runs, write_summary_csv
from evalkit.report import render_report
from evalkit.stats import summarize
from evalkit.tasks.schema import Task


class ToolsetProvider(Protocol):
    def server_source(self, condition: Condition, sandbox_base_url: str) -> str:
        ...


@dataclass
class FixtureProvider:
    """Offline provider: renders a fixed local tool surface (ignores the condition).

    Useful for harness verification and an approximate naive baseline without a backend.
    """
    app_name: str = PETSTORE_APP_NAME
    tools: list | None = None

    def server_source(self, condition: Condition, sandbox_base_url: str) -> str:
        return render_python_server(self.app_name, sandbox_base_url,
                                    self.tools or PETSTORE_NAIVE_TOOLS)


@dataclass
class LiveProvider:
    """Live provider: SYNAPSE generates the tool set for each condition."""
    client: object               # SynapseClient (kept loose to avoid import cycle)
    api_name: str
    spec_path: str
    source_format: str = "openapi3"
    timeout_s: int = 600

    def server_source(self, condition: Condition, sandbox_base_url: str) -> str:
        res = self.client.synthesize(
            api_name=self.api_name, spec_path=self.spec_path,
            sandbox_base_url=sandbox_base_url, condition=condition,
            source_format=self.source_format, timeout_s=self.timeout_s,
        )
        return res.server_py


async def _run_condition(condition: Condition, tasks: list[Task], provider: ToolsetProvider,
                         model: ChatModel, sandbox: SandboxServer, repeats: int,
                         max_steps: int) -> list[RunResult]:
    source = provider.server_source(condition, sandbox.base_url)
    runner = AgentRunner(model, max_steps=max_steps)
    judge = Judge(lambda: sandbox.store.snapshot())
    results: list[RunResult] = []

    with GeneratedServer(source) as gs:
        async with McpToolClient(gs) as mcp:
            tool_specs = await mcp.list_tools()
            for task in tasks:
                for rep in range(repeats):
                    sandbox.reset()
                    run = await runner.run(task=task, tool_specs=tool_specs, mcp=mcp,
                                           condition_key=condition.key, repeat=rep)
                    ok, reason = judge.judge(task)
                    run.success, run.judge_reason = ok, reason
                    results.append(run)
    return results


def run_experiment(*, spec_path: str, tasks: list[Task], provider: ToolsetProvider,
                   model: ChatModel, out_dir: str, conditions: list[Condition] | None = None,
                   repeats: int = 3, max_steps: int = 12, seed: int = 42,
                   title: str = "SYNAPSE ablation") -> dict:
    conditions = conditions or ALL_CONDITIONS
    all_results: list[RunResult] = []

    with SandboxServer(spec_path, seed=seed) as sandbox:
        for condition in conditions:
            # Fresh event loop per condition; the sandbox runs in its own thread.
            results = asyncio.run(
                _run_condition(condition, tasks, provider, model, sandbox, repeats, max_steps)
            )
            all_results.extend(results)

    artifacts = render_report(all_results, out_dir, title=title)
    write_runs(all_results, f"{out_dir}/runs.jsonl")
    write_summary_csv(summarize(all_results), f"{out_dir}/summary.csv")
    return {"results": all_results, "artifacts": artifacts,
            "summary": summarize(all_results)}

"""The agent loop: give the model a task + a tool set, let it act, record everything.

The runner is deliberately model-agnostic (takes a ``ChatModel``) and routes every tool
call through the live MCP client to the real generated server. It measures the things
the ablation compares: tool-call count, distinct tools, selection errors, steps, tokens,
latency. Success itself is decided afterward by the Judge against sandbox state.
"""
from __future__ import annotations

import json
import time
from typing import Any

from evalkit.agent.llm import AssistantTurn, ChatModel
from evalkit.agent.transcript import RunResult, Step
from evalkit.mcp_bridge import McpToolClient, McpToolError
from evalkit.tasks.schema import Task
from evalkit.toolset import to_openai_tools

SYSTEM_PROMPT = (
    "You are an API agent. Use the provided tools to accomplish the user's request "
    "completely. Chain calls when needed (e.g. create a resource, then use the id it "
    "returns). When the task is fully done, reply with a short confirmation and no "
    "further tool calls. Do not ask clarifying questions; act on reasonable defaults."
)


class AgentRunner:
    def __init__(self, model: ChatModel, max_steps: int = 12) -> None:
        self._model = model
        self._max_steps = max_steps

    async def run(self, *, task: Task, tool_specs: list, mcp: McpToolClient,
                  condition_key: str, repeat: int) -> RunResult:
        tools = to_openai_tools(tool_specs)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task.instruction},
        ]
        result = RunResult(
            api=task.api, condition_key=condition_key, task_id=task.id,
            repeat=repeat, tool_count=len(tool_specs),
        )
        used_tools: set[str] = set()
        start = time.monotonic()

        try:
            for _ in range(self._max_steps):
                turn: AssistantTurn = self._model.complete(messages, tools)
                result.prompt_tokens += turn.prompt_tokens
                result.completion_tokens += turn.completion_tokens
                result.steps_used += 1

                if not turn.tool_calls:
                    result.final_text = turn.content or ""
                    result.steps.append(Step(kind="final"))
                    break

                messages.append(_assistant_message(turn))
                for call in turn.tool_calls:
                    result.tool_calls += 1
                    used_tools.add(call.name)
                    ok, payload = await self._invoke(mcp, call.name, call.arguments)
                    if not ok:
                        result.selection_errors += 1
                    result.steps.append(Step(kind="tool_call", tool_name=call.name,
                                             arguments=call.arguments, ok=ok,
                                             error=None if ok else str(payload)))
                    messages.append({
                        "role": "tool", "tool_call_id": call.id,
                        "content": json.dumps(payload)[:4000],
                    })
        except Exception as exc:  # defensive: a run failing shouldn't kill the sweep
            result.error = f"{type(exc).__name__}: {exc}"

        result.distinct_tools = len(used_tools)
        result.latency_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _invoke(self, mcp: McpToolClient, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        try:
            return True, await mcp.call_tool(name, args)
        except McpToolError as exc:
            return False, str(exc)
        except Exception as exc:  # unknown tool name, transport, etc.
            return False, f"{type(exc).__name__}: {exc}"


def _assistant_message(turn: AssistantTurn) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": turn.content or ""}
    if turn.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in turn.tool_calls
        ]
    return msg

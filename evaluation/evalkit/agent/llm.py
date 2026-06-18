"""LLM abstraction for the agent loop.

A thin `ChatModel` protocol sits between the runner and whatever produces assistant
turns. ``OpenAIChat`` is the real, pinned GPT-4o function-caller. ``MockChat`` takes a
deterministic policy function so the entire runner -> MCP -> sandbox -> judge path can be
tested with no API key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatModel(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantTurn:
        ...


class OpenAIChat:
    """Pinned, deterministic GPT-4o function-calling client."""

    def __init__(self, model: str, temperature: float = 0.0, seed: int = 42,
                 max_tokens: int = 1024, api_key: str | None = None) -> None:
        import openai  # imported lazily so the harness loads without the key/SDK
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantTurn:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            temperature=self._temperature,
            seed=self._seed,
            max_tokens=self._max_tokens,
        )
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name,
                     arguments=_safe_json(tc.function.arguments))
            for tc in (msg.tool_calls or [])
        ]
        usage = resp.usage
        return AssistantTurn(
            content=msg.content,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


class MockChat:
    """Deterministic chat driven by a policy: (messages, tools) -> AssistantTurn.

    The policy may inspect prior tool results in ``messages`` (e.g. to read an id
    returned by a create call), making keyless end-to-end tests realistic.
    """

    def __init__(self, policy: Callable[[list[dict[str, Any]], list[dict[str, Any]]], AssistantTurn]) -> None:
        self._policy = policy
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantTurn:
        self.calls += 1
        return self._policy(messages, tools)


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}

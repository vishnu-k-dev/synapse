"""Per-run records — the unit of measurement.

Every (api, condition, task, repeat) tuple produces one RunResult. These are what get
persisted and aggregated into the ablation curve. Keeping the per-step trace lets us
later mine tool co-occurrence for Phase 6 (task-aware compression).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Step:
    kind: str                      # "tool_call" | "final"
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    ok: bool | None = None         # did the tool call succeed (no transport/schema error)
    error: str | None = None


@dataclass
class RunResult:
    api: str
    condition_key: str
    task_id: str
    repeat: int
    tool_count: int                # |toolset| exposed to the agent (the independent variable)

    success: bool = False
    judge_reason: str = ""
    tool_calls: int = 0            # total tool invocations
    distinct_tools: int = 0
    selection_errors: int = 0      # calls that errored (bad name/args/transport)
    steps_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    final_text: str = ""
    error: str | None = None
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = [asdict(s) for s in self.steps]
        return d

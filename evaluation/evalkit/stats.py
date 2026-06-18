"""Aggregate RunResults into the numbers that make the claim defensible.

Per condition: success rate, mean tool-calls/task, mean tokens — each with a bootstrap
95% CI. Across conditions (paired by task+repeat): McNemar on success and Wilcoxon on
tool-calls, so we can say "the difference is significant", not just "it looks bigger".
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable

import numpy as np

from evalkit.agent.transcript import RunResult


@dataclass
class ConditionSummary:
    condition_key: str
    n: int
    tool_count: float            # mean |toolset| exposed
    success_rate: float
    success_ci: tuple[float, float]
    mean_tool_calls: float
    tool_calls_ci: tuple[float, float]
    mean_tokens: float


def _bootstrap_ci(values: list[float], n_boot: int = 2000, alpha: float = 0.05,
                  seed: int = 42) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    boots = rng.choice(arr, size=(n_boot, arr.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (round(float(lo), 4), round(float(hi), 4))


def summarize_condition(results: list[RunResult]) -> ConditionSummary:
    successes = [1.0 if r.success else 0.0 for r in results]
    tool_calls = [float(r.tool_calls) for r in results]
    tokens = [float(r.prompt_tokens + r.completion_tokens) for r in results]
    tool_counts = [float(r.tool_count) for r in results]
    return ConditionSummary(
        condition_key=results[0].condition_key,
        n=len(results),
        tool_count=round(mean(tool_counts), 2) if tool_counts else 0.0,
        success_rate=round(mean(successes), 4) if successes else 0.0,
        success_ci=_bootstrap_ci(successes),
        mean_tool_calls=round(mean(tool_calls), 3) if tool_calls else 0.0,
        tool_calls_ci=_bootstrap_ci(tool_calls),
        mean_tokens=round(mean(tokens), 1) if tokens else 0.0,
    )


def summarize(results: Iterable[RunResult]) -> dict[str, ConditionSummary]:
    by_cond: dict[str, list[RunResult]] = {}
    for r in results:
        by_cond.setdefault(r.condition_key, []).append(r)
    return {k: summarize_condition(v) for k, v in by_cond.items()}


# ── paired significance tests (baseline vs each condition) ─────────────────────

def _pair_key(r: RunResult) -> tuple[str, str, int]:
    return (r.api, r.task_id, r.repeat)


def mcnemar_success(baseline: list[RunResult], other: list[RunResult]) -> float | None:
    """Exact McNemar p-value on paired success outcomes (None if no discordant pairs)."""
    b = {_pair_key(r): r.success for r in baseline}
    o = {_pair_key(r): r.success for r in other}
    shared = b.keys() & o.keys()
    n01 = sum(1 for k in shared if not b[k] and o[k])   # baseline fail, other success
    n10 = sum(1 for k in shared if b[k] and not o[k])   # baseline success, other fail
    if n01 + n10 == 0:
        return None
    try:
        from scipy.stats import binomtest
        return float(binomtest(min(n01, n10), n01 + n10, 0.5).pvalue)
    except Exception:
        return None


def wilcoxon_tool_calls(baseline: list[RunResult], other: list[RunResult]) -> float | None:
    b = {_pair_key(r): r.tool_calls for r in baseline}
    o = {_pair_key(r): r.tool_calls for r in other}
    shared = sorted(b.keys() & o.keys())
    diffs = [b[k] - o[k] for k in shared]
    if not any(diffs):
        return None
    try:
        from scipy.stats import wilcoxon
        return float(wilcoxon([b[k] for k in shared], [o[k] for k in shared]).pvalue)
    except Exception:
        return None

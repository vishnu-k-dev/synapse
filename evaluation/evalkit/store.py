"""Persist results. Raw runs -> JSONL (one line per run, full step trace); summaries ->
CSV. Stdlib only, so results are portable and diff-able without a DB. (Parquet/DuckDB
can be layered on later for large sweeps.)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from evalkit.agent.transcript import RunResult
from evalkit.stats import ConditionSummary


def write_runs(results: Iterable[RunResult], path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict()) + "\n")
    return str(p)


def write_summary_csv(summaries: dict[str, ConditionSummary], path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = ["condition_key", "n", "tool_count", "success_rate", "success_ci_lo",
            "success_ci_hi", "mean_tool_calls", "tool_calls_ci_lo", "tool_calls_ci_hi",
            "mean_tokens"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in summaries.values():
            w.writerow([s.condition_key, s.n, s.tool_count, s.success_rate,
                        s.success_ci[0], s.success_ci[1], s.mean_tool_calls,
                        s.tool_calls_ci[0], s.tool_calls_ci[1], s.mean_tokens])
    return str(p)

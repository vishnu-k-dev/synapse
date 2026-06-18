"""Render the ablation result: a markdown table always, the curve PNG if matplotlib is
available. The headline artifact is "success rate & tool-calls vs condition".
"""
from __future__ import annotations

from pathlib import Path

from evalkit.agent.transcript import RunResult
from evalkit.conditions import ALL_CONDITIONS
from evalkit.stats import (
    ConditionSummary,
    mcnemar_success,
    summarize,
    wilcoxon_tool_calls,
)

# Stable condition ordering for tables/plots.
_ORDER = [c.key for c in ALL_CONDITIONS]
_LABEL = {c.key: c.label for c in ALL_CONDITIONS}


def _ordered(summaries: dict[str, ConditionSummary]) -> list[ConditionSummary]:
    return [summaries[k] for k in _ORDER if k in summaries]


def markdown_table(summaries: dict[str, ConditionSummary]) -> str:
    rows = [
        "| Condition | Tools | Success rate (95% CI) | Tool-calls/task (95% CI) | Tokens |",
        "|-----------|------:|-----------------------|--------------------------|-------:|",
    ]
    for s in _ordered(summaries):
        rows.append(
            f"| {_LABEL.get(s.condition_key, s.condition_key)} | {s.tool_count:g} | "
            f"{s.success_rate:.0%} [{s.success_ci[0]:.0%}, {s.success_ci[1]:.0%}] | "
            f"{s.mean_tool_calls:.2f} [{s.tool_calls_ci[0]:.2f}, {s.tool_calls_ci[1]:.2f}] | "
            f"{s.mean_tokens:.0f} |"
        )
    return "\n".join(rows)


def significance_block(results: list[RunResult]) -> str:
    by_cond: dict[str, list[RunResult]] = {}
    for r in results:
        by_cond.setdefault(r.condition_key, []).append(r)
    baseline_key = "C1_naive"
    if baseline_key not in by_cond:
        return ""
    base = by_cond[baseline_key]
    lines = ["", "Significance vs naive baseline (paired):"]
    for key in _ORDER:
        if key == baseline_key or key not in by_cond:
            continue
        p_succ = mcnemar_success(base, by_cond[key])
        p_calls = wilcoxon_tool_calls(base, by_cond[key])
        lines.append(
            f"  - {_LABEL.get(key, key)}: success McNemar p="
            f"{'n/a' if p_succ is None else f'{p_succ:.4f}'}, "
            f"tool-calls Wilcoxon p={'n/a' if p_calls is None else f'{p_calls:.4f}'}"
        )
    return "\n".join(lines)


def render_report(results: list[RunResult], out_dir: str, title: str = "SYNAPSE ablation") -> dict[str, str]:
    summaries = summarize(results)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    md = f"# {title}\n\n{markdown_table(summaries)}\n{significance_block(results)}\n"
    md_path = out / "report.md"
    md_path.write_text(md, encoding="utf-8")

    artifacts = {"markdown": str(md_path)}
    png = _maybe_plot(summaries, out / "ablation_curve.png", title)
    if png:
        artifacts["figure"] = png
    return artifacts


def _maybe_plot(summaries: dict[str, ConditionSummary], path: Path, title: str) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    ordered = _ordered(summaries)
    if not ordered:
        return None
    labels = [_LABEL.get(s.condition_key, s.condition_key) for s in ordered]
    success = [s.success_rate * 100 for s in ordered]
    calls = [s.mean_tool_calls for s in ordered]

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.bar(labels, success, color="#4C78A8", alpha=0.85)
    ax1.set_ylabel("Task success rate (%)", color="#4C78A8")
    ax1.set_ylim(0, 100)
    ax2 = ax1.twinx()
    ax2.plot(labels, calls, color="#E45756", marker="o", linewidth=2)
    ax2.set_ylabel("Mean tool-calls / task", color="#E45756")
    ax1.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)

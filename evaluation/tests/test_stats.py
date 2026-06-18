"""Unit tests for the aggregation + significance layer (synthetic RunResults)."""
from __future__ import annotations

from evalkit.agent.transcript import RunResult
from evalkit.report import markdown_table, render_report
from evalkit.stats import mcnemar_success, summarize, wilcoxon_tool_calls


def _mk(cond: str, task: str, success: bool, calls: int, tool_count: int = 10) -> RunResult:
    return RunResult(api="petstore", condition_key=cond, task_id=task, repeat=0,
                     tool_count=tool_count, success=success, tool_calls=calls,
                     prompt_tokens=100, completion_tokens=20)


def test_summarize_basic():
    results = [_mk("C1_naive", f"t{i}", i % 2 == 0, 5) for i in range(4)]
    s = summarize(results)["C1_naive"]
    assert s.n == 4
    assert 0.0 <= s.success_ci[0] <= s.success_ci[1] <= 1.0
    assert s.mean_tool_calls == 5.0
    assert s.tool_count == 10


def test_mcnemar_detects_improvement():
    base = [_mk("C1_naive", f"t{i}", False, 6) for i in range(6)]
    other = [_mk("C4_full", f"t{i}", True, 2) for i in range(6)]
    p = mcnemar_success(base, other)        # all discordant (fail->success)
    assert p is not None and 0.0 <= p <= 0.05


def test_wilcoxon_on_tool_calls():
    base = [_mk("C1_naive", f"t{i}", True, 6) for i in range(6)]
    other = [_mk("C4_full", f"t{i}", True, 2 + (i % 2)) for i in range(6)]
    p = wilcoxon_tool_calls(base, other)
    assert p is not None and 0.0 <= p <= 1.0


def test_render_report_writes_files(tmp_path):
    results = ([_mk("C1_naive", f"t{i}", i % 3 != 0, 6, tool_count=16) for i in range(6)] +
               [_mk("C4_full", f"t{i}", True, 2, tool_count=5) for i in range(6)])
    artifacts = render_report(results, str(tmp_path), title="test")
    assert (tmp_path / "report.md").exists()
    md = markdown_table(summarize(results))
    assert "Naive" in md and "Full SYNAPSE" in md

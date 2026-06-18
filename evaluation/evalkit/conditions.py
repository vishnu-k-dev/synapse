"""The 2x2 factorial ablation that is the heart of the evaluation.

The independent variable is *tool-set design*: we hold the API, the agent, the
task suite, and the seed fixed, and vary only how SYNAPSE shapes the tool surface.
Each condition is nothing more than a different ``pipeline_config`` payload sent to
``POST /api/v1/apps`` — the backend already supports every cell (compression's
``_passthrough`` implements the naive baseline; the two flags live on
``PipelineConfig``). No backend changes are required to run the ablation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    key: str                       # short stable id, e.g. "C4_full"
    label: str                     # human label for plots/tables
    enable_compression: bool
    enable_workflow_discovery: bool

    def pipeline_config(self, synthesis_target: str = "python") -> dict:
        """The exact ``pipeline_config`` block for CreateAppRequest."""
        return {
            "synthesis_target": synthesis_target,
            "enable_compression": self.enable_compression,
            "enable_workflow_discovery": self.enable_workflow_discovery,
        }


# The four cells of the factorial. C1 is the "naive MCP" strawman SYNAPSE argues
# against; C4 is full SYNAPSE. C2/C3 isolate each mechanism's individual effect.
NAIVE = Condition("C1_naive", "Naive (1 tool/endpoint)", False, False)
COMPRESSION_ONLY = Condition("C2_compression", "+Compression", True, False)
WORKFLOW_ONLY = Condition("C3_workflows", "+Workflows", False, True)
FULL = Condition("C4_full", "Full SYNAPSE", True, True)

ALL_CONDITIONS: list[Condition] = [NAIVE, COMPRESSION_ONLY, WORKFLOW_ONLY, FULL]

CONDITIONS_BY_KEY: dict[str, Condition] = {c.key: c for c in ALL_CONDITIONS}

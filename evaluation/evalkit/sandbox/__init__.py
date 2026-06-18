"""Spec-driven mock sandbox.

Turns any OpenAPI spec into a deterministic, stateful REST backend so the agent's
tool calls hit a free, seedable target instead of a live API. This is what makes a
1600-run ablation cheap and byte-reproducible, and what lets multi-step tasks chain
(POST returns an id -> later calls consume it).
"""

from evalkit.sandbox.state import EntityStore
from evalkit.sandbox.simulator import build_simulator, parse_route
from evalkit.sandbox.runner import SandboxServer

__all__ = ["EntityStore", "build_simulator", "parse_route", "SandboxServer"]

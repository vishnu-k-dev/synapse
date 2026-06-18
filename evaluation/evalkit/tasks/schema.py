"""Task + success-oracle schema.

A task is a natural-language instruction plus a *programmatic* oracle that asserts on
final sandbox state — deterministic, no LLM-judge needed for the common case. Oracles
support a relational reference (`where_ref`) so workflow tasks can assert that, e.g., a
pet's owner_id equals the id of the owner the agent just created (the id-chaining that
distinguishes a real multi-step success from a lucky single call).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Ref:
    """Resolve to the value of ``field`` on an entity in ``collection`` matching ``match``."""
    collection: str
    match: dict[str, Any]
    field: str = "id"


@dataclass
class StateAssertion:
    collection: str
    where: dict[str, Any] = field(default_factory=dict)
    where_ref: dict[str, Ref] = field(default_factory=dict)
    count: int | None = None       # None => expect >= 1 match; otherwise exact count


@dataclass
class Task:
    id: str
    api: str
    instruction: str
    difficulty: str                # "single" | "multi" | "workflow"
    oracle: list[StateAssertion] = field(default_factory=list)


def _parse_assertion(raw: dict[str, Any]) -> StateAssertion:
    refs = {
        k: Ref(collection=v["collection"], match=v["match"], field=v.get("field", "id"))
        for k, v in (raw.get("where_ref") or {}).items()
    }
    return StateAssertion(
        collection=raw["collection"],
        where=raw.get("where") or {},
        where_ref=refs,
        count=raw.get("count"),
    )


def load_suite(path: str, api: str) -> list[Task]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    tasks: list[Task] = []
    for raw in data:
        tasks.append(Task(
            id=raw["id"],
            api=api,
            instruction=raw["instruction"],
            difficulty=raw.get("difficulty", "single"),
            oracle=[_parse_assertion(a) for a in (raw.get("oracle") or [])],
        ))
    return tasks

"""Success oracle: assert on final sandbox state.

Deterministic and programmatic — it reads the sandbox's in-process store snapshot and
checks each assertion. Relational `where_ref` assertions are resolved first (e.g. look
up the id of the owner named "Bob"), then matched against candidate entities. This is
what makes "did the agent actually complete the workflow?" an objective yes/no.
"""
from __future__ import annotations

from typing import Any, Callable

from evalkit.tasks.schema import Ref, StateAssertion, Task

StateProvider = Callable[[], dict[str, list[dict[str, Any]]]]


class Judge:
    def __init__(self, state_provider: StateProvider) -> None:
        self._state = state_provider

    def judge(self, task: Task) -> tuple[bool, str]:
        state = self._state()
        for a in task.oracle:
            ok, reason = self._check(a, state)
            if not ok:
                return False, reason
        return True, "all assertions satisfied"

    def _check(self, a: StateAssertion, state: dict[str, list[dict[str, Any]]]) -> tuple[bool, str]:
        items = state.get(a.collection, [])
        resolved_refs: dict[str, Any] = {}
        for field_name, ref in a.where_ref.items():
            value = self._resolve_ref(ref, state)
            if value is None:
                return False, f"unresolved where_ref {field_name} -> {ref.collection}{ref.match}"
            resolved_refs[field_name] = value

        matches = [
            e for e in items
            if _matches(e, a.where) and all(str(e.get(k)) == str(v) for k, v in resolved_refs.items())
        ]
        if a.count is None:
            if not matches:
                return False, f"no {a.collection} matching {a.where or resolved_refs}"
        elif len(matches) != a.count:
            return False, f"{a.collection}: expected {a.count} matching, found {len(matches)}"
        return True, ""

    def _resolve_ref(self, ref: Ref, state: dict[str, list[dict[str, Any]]]) -> Any:
        for e in state.get(ref.collection, []):
            if _matches(e, ref.match):
                return e.get(ref.field)
        return None


def _matches(entity: dict[str, Any], where: dict[str, Any]) -> bool:
    return all(str(entity.get(k)) == str(v) for k, v in where.items())

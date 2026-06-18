"""In-memory, deterministic entity store backing the mock sandbox.

Implements just enough REST semantics for agent tasks to be meaningful:
create/read/update/delete/list, plus parent-scoped listing for nested routes.
Ids are generated from a per-collection counter (``pets_1``, ``pets_2`` ...) so a
fresh store with the same call sequence always yields the same ids — essential for
reproducible runs and for success oracles that assert on concrete state.
"""
from __future__ import annotations

import re
from typing import Any

_PAGINATION_KEYS = {"limit", "offset", "page", "per_page", "cursor", "sort", "order", "fields"}

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    """``vetId`` -> ``vet_id`` so query params line up with stored snake_case fields."""
    return _CAMEL_RE.sub("_", name).lower().replace("-", "_")


class EntityStore:
    """A tiny document store: ``collection -> {id -> entity}``."""

    def __init__(self, seed: int = 42, id_field: str = "id") -> None:
        self.seed = seed
        self.id_field = id_field
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._counters: dict[str, int] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def reset(self) -> None:
        self._data.clear()
        self._counters.clear()

    # ── mutations ─────────────────────────────────────────────────────────────
    def _next_id(self, collection: str) -> str:
        self._counters[collection] = self._counters.get(collection, 0) + 1
        return f"{collection}_{self._counters[collection]}"

    def create(self, collection: str, body: dict[str, Any] | None) -> dict[str, Any]:
        entity = dict(body or {})
        eid = str(entity.get(self.id_field) or self._next_id(collection))
        entity[self.id_field] = eid
        self._data.setdefault(collection, {})[eid] = entity
        return entity

    def update(self, collection: str, eid: str, body: dict[str, Any] | None) -> dict[str, Any] | None:
        bucket = self._data.get(collection, {})
        if eid not in bucket:
            return None
        bucket[eid].update({k: v for k, v in (body or {}).items() if k != self.id_field})
        return bucket[eid]

    def delete(self, collection: str, eid: str) -> bool:
        return self._data.get(collection, {}).pop(eid, None) is not None

    # ── reads ─────────────────────────────────────────────────────────────────
    def get(self, collection: str, eid: str) -> dict[str, Any] | None:
        return self._data.get(collection, {}).get(eid)

    def list(
        self,
        collection: str,
        filters: dict[str, Any] | None = None,
        parent_field: str | None = None,
        parent_value: str | None = None,
    ) -> list[dict[str, Any]]:
        items = list(self._data.get(collection, {}).values())
        if parent_field and parent_value is not None:
            items = [e for e in items if str(e.get(parent_field)) == str(parent_value)]
        for raw_key, value in (filters or {}).items():
            if raw_key in _PAGINATION_KEYS:
                continue
            field = camel_to_snake(raw_key)
            items = [e for e in items if _matches(e, field, value)]
        return items

    # ── introspection (for success oracles) ───────────────────────────────────
    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {col: list(bucket.values()) for col, bucket in self._data.items()}


def _matches(entity: dict[str, Any], field: str, value: Any) -> bool:
    if field not in entity:
        return True  # unknown filter field -> don't exclude (mock is permissive)
    return str(entity[field]) == str(value)

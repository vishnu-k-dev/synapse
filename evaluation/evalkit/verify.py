"""Phase 6 — generated-server verification.

Asserts each generated MCP tool (a) exposes a well-formed input schema and (b) faithfully
*round-trips a real call* against the sandbox. This generalizes finding F-1: any tool whose
generated signature, routing, or return handling is broken fails here instead of silently
shipping. Produces a per-API "synthesis correctness" score.

It drives a CRUD scenario inferred from tool names + schemas: create entities first, then
exercise list/read/update with the real returned ids, deletes last. Assumes RESTful tool
shapes (one verb per tool) — the common case for naive and entity-scoped compressed tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evalkit.mcp_bridge import McpToolClient, ToolSpec

_VERBS = {
    "create": ("create_", "add_", "register_", "new_", "post_"),
    "list": ("list_", "search_", "find_", "all_"),
    "read": ("get_", "read_", "fetch_", "retrieve_", "show_"),
    "update": ("update_", "edit_", "set_", "put_", "patch_", "modify_"),
    "delete": ("delete_", "remove_", "cancel_", "destroy_"),
}


def classify(name: str) -> str:
    low = name.lower()
    for kind, prefixes in _VERBS.items():
        if low.startswith(prefixes):
            return kind
    return "unknown"


def noun_of(name: str) -> str:
    low = name.lower()
    for prefixes in _VERBS.values():
        for p in prefixes:
            if low.startswith(p):
                low = low[len(p):]
                break
    low = low.strip("_")
    return low[:-1] if low.endswith("s") else low      # crude singularize


def _collection_of_id(eid: str) -> str:
    # sandbox ids are "{collection}_{n}" -> collection -> singular
    coll = eid.rsplit("_", 1)[0] if "_" in eid else eid
    return coll[:-1] if coll.endswith("s") else coll


def _is_id_field(field: str) -> bool:
    f = field.lower()
    return f == "id" or f.endswith("_id") or f.endswith("id")


def _dummy(json_type: str | None, field: str) -> Any:
    return {"string": f"verify_{field}", "integer": 1, "number": 1.0,
            "boolean": True, "array": [], "object": {}}.get(json_type or "string", f"verify_{field}")


def synth_args(schema: dict[str, Any] | None, id_value: str | None = None) -> dict[str, Any]:
    """Fill required params with schema-valid dummy values; id-like params get id_value."""
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    args: dict[str, Any] = {}
    for name, spec in props.items():
        if name not in required:
            continue
        if id_value is not None and _is_id_field(name):
            args[name] = id_value
        else:
            t = spec.get("type") if isinstance(spec, dict) else "string"
            args[name] = _dummy(t, name)
    return args


@dataclass
class ToolCheck:
    name: str
    kind: str
    ok: bool
    detail: str = ""


@dataclass
class VerificationReport:
    api: str
    checks: list[ToolCheck] = field(default_factory=list)

    @property
    def definitive(self) -> list[ToolCheck]:
        return [c for c in self.checks if c.kind != "indeterminate"]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.definitive if c.ok)

    @property
    def total(self) -> int:
        return len(self.definitive)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0


def _schema_valid(spec: ToolSpec) -> bool:
    s = spec.input_schema
    return isinstance(s, dict) and s.get("type") == "object" and isinstance(s.get("properties"), dict)


async def verify(tool_specs: list[ToolSpec], mcp: McpToolClient, sandbox: Any,
                 api: str) -> VerificationReport:
    """Run the verification scenario. ``sandbox`` exposes ``.reset()`` + ``.store``."""
    report = VerificationReport(api=api)
    sandbox.reset()

    by_kind: dict[str, list[ToolSpec]] = {}
    for t in tool_specs:
        by_kind.setdefault(classify(t.name), []).append(t)

    # All tools must at least advertise a valid schema.
    for t in tool_specs:
        if not _schema_valid(t):
            report.checks.append(ToolCheck(t.name, "schema", False, "malformed input schema"))

    ids_by_noun: dict[str, list[str]] = {}

    # Pass 1 — creates. Capture returned ids (and the collection they belong to).
    for t in by_kind.get("create", []):
        ok, detail = await _call_ok(mcp, t, synth_args(t.input_schema))
        result_id = detail if ok and isinstance(detail, str) and detail.startswith("id=") else None
        if ok:
            ok = result_id is not None
            if result_id:
                eid = result_id[3:]
                ids_by_noun.setdefault(_collection_of_id(eid), []).append(eid)
        report.checks.append(ToolCheck(t.name, "create", ok,
                                       "no id returned" if not ok else "created"))

    def pick_id(t: ToolSpec) -> str | None:
        # Only target an entity of the *matching* collection. If none was created (e.g. a
        # read-only entity like vets), return None -> the check is marked indeterminate
        # rather than failed on a wrong-collection id (which would be a false negative).
        pool = ids_by_noun.get(noun_of(t.name))
        return pool[0] if pool else None

    # Pass 2 — list / read / update (non-destructive).
    for kind in ("list", "read", "update"):
        for t in by_kind.get(kind, []):
            needs_id = any(_is_id_field(p) for p in (t.input_schema.get("required") or []))
            eid = pick_id(t) if needs_id else None
            if needs_id and eid is None:
                report.checks.append(ToolCheck(t.name, "indeterminate", True, "no entity to target"))
                continue
            ok, detail = await _call_ok(mcp, t, synth_args(t.input_schema, eid),
                                        expect_list=(kind == "list"))
            report.checks.append(ToolCheck(t.name, kind, ok, detail if not ok else "ok"))

    # Pass 3 — deletes last (so they don't strand other checks).
    for t in by_kind.get("delete", []):
        eid = pick_id(t)
        if eid is None:
            report.checks.append(ToolCheck(t.name, "indeterminate", True, "no entity to delete"))
            continue
        ok, detail = await _call_ok(mcp, t, synth_args(t.input_schema, eid))
        report.checks.append(ToolCheck(t.name, "delete", ok, detail if not ok else "ok"))

    # Unknown-verb tools: schema-checked above; attempt a best-effort call.
    for t in by_kind.get("unknown", []):
        ok, detail = await _call_ok(mcp, t, synth_args(t.input_schema))
        report.checks.append(ToolCheck(t.name, "indeterminate", True, f"best-effort: {detail}"))

    return report


async def _call_ok(mcp: McpToolClient, t: ToolSpec, args: dict[str, Any],
                   expect_list: bool = False) -> tuple[bool, str]:
    try:
        res = await mcp.call_tool(t.name, args)
    except Exception as exc:  # McpToolError (e.g. F-1 list crash), transport, etc.
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    if expect_list:
        # An empty list comes back as "" (FastMCP emits no text content for []), which is a
        # valid empty result. A list tool that genuinely breaks raises above (the F-1 guard).
        ok = isinstance(res, list) or res in ("", None)
        return ok, ("ok" if ok else f"expected list, got {type(res).__name__}")
    if isinstance(res, dict) and "id" in res:
        return True, f"id={res['id']}"
    return True, "ok"

"""Build a deterministic FastAPI mock backend from an OpenAPI spec.

The simulator is generic: it does not know "pets" from "invoices". For every path in
the spec it infers a *collection* and whether the path is collection-level (list/create)
or item-level (read/update/delete) from the URL structure, then serves standard REST
semantics over an :class:`EntityStore`. That genericity is the point — the same code
backs every API in the benchmark.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from evalkit.sandbox.state import EntityStore, camel_to_snake

_METHODS = ("get", "post", "put", "patch", "delete")


def _singular(collection: str) -> str:
    """Crude singularizer good enough for ``owners`` -> ``owner`` parent fields."""
    if collection.endswith("ies"):
        return collection[:-3] + "y"
    if collection.endswith("s") and not collection.endswith("ss"):
        return collection[:-1]
    return collection


@dataclass(frozen=True)
class RouteInfo:
    path: str
    collection: str
    item_level: bool
    id_param: str | None
    parent_collection: str | None
    parent_param: str | None

    @property
    def parent_field(self) -> str | None:
        if not self.parent_collection:
            return None
        return f"{_singular(self.parent_collection)}_id"


def parse_route(path: str) -> RouteInfo:
    """Infer (collection, level, parent) from a templated path.

    ``/pets``                       -> collection=pets, item-level=False
    ``/pets/{petId}``               -> collection=pets, item-level=True, id=petId
    ``/owners/{ownerId}/pets``      -> collection=pets, parent=(owners, ownerId)
    ``/owners/{ownerId}/pets/{id}`` -> collection=pets, item-level=True, parent=(owners,…)
    """
    segments = [s for s in path.strip("/").split("/") if s]
    pairs: list[tuple[str, str | None]] = []  # (collection, id_param|None)
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg.startswith("{"):
            # Orphan param (path starting with a param) — attach to previous pair.
            if pairs:
                col, _ = pairs[-1]
                pairs[-1] = (col, seg.strip("{}"))
            i += 1
            continue
        param: str | None = None
        if i + 1 < len(segments) and segments[i + 1].startswith("{"):
            param = segments[i + 1].strip("{}")
            i += 2
        else:
            i += 1
        pairs.append((seg, param))

    if not pairs:  # pragma: no cover - empty path
        return RouteInfo(path, "root", False, None, None, None)

    collection, id_param = pairs[-1]
    parent_collection, parent_param = (pairs[-2] if len(pairs) >= 2 else (None, None))
    return RouteInfo(
        path=path,
        collection=collection,
        item_level=id_param is not None,
        id_param=id_param,
        parent_collection=parent_collection,
        parent_param=parent_param,
    )


def _load_spec(spec_path: str) -> dict[str, Any]:
    text = open(spec_path, "r", encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


async def _json_body(request: Request) -> dict[str, Any]:
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            payload = await request.json()
            return payload if isinstance(payload, dict) else {"value": payload}
        except Exception:
            return {}
    return {}


def _make_handler(route: RouteInfo, method: str, store: EntityStore):
    method = method.upper()

    async def handler(request: Request) -> Response:
        pp = request.path_params
        body = await _json_body(request)
        col = route.collection

        if route.item_level:
            eid = str(pp.get(route.id_param)) if route.id_param else None
            if method == "GET":
                entity = store.get(col, eid) if eid else None
                return JSONResponse(entity) if entity else _not_found(col, eid)
            if method in ("PUT", "PATCH"):
                entity = store.update(col, eid, body) if eid else None
                return JSONResponse(entity) if entity else _not_found(col, eid)
            if method == "DELETE":
                return Response(status_code=204) if (eid and store.delete(col, eid)) else _not_found(col, eid)

        else:  # collection level
            if method == "POST":
                if route.parent_field and route.parent_param:
                    body.setdefault(route.parent_field, str(pp.get(route.parent_param)))
                return JSONResponse(store.create(col, body), status_code=201)
            if method == "GET":
                parent_value = str(pp.get(route.parent_param)) if route.parent_param else None
                items = store.list(
                    col,
                    filters=dict(request.query_params),
                    parent_field=route.parent_field,
                    parent_value=parent_value,
                )
                return JSONResponse(items)

        return JSONResponse({"error": f"{method} not supported on {route.path}"}, status_code=405)

    return handler


def _not_found(collection: str, eid: str | None) -> JSONResponse:
    return JSONResponse({"error": f"{_singular(collection)} '{eid}' not found"}, status_code=404)


def build_simulator(spec_path: str, seed: int = 42) -> tuple[FastAPI, EntityStore]:
    """Construct a FastAPI app + its backing store from an OpenAPI spec.

    Returns the app and the store; the store is exposed so success oracles can read
    final state directly in-process (and over HTTP via ``/__sandbox__/state``).
    """
    spec = _load_spec(spec_path)
    store = EntityStore(seed=seed)
    app = FastAPI(title=f"SYNAPSE sandbox: {spec.get('info', {}).get('title', 'api')}")

    paths = spec.get("paths") or {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        route = parse_route(path)
        for method in _METHODS:
            if method in item:
                app.add_api_route(
                    path,
                    _make_handler(route, method, store),
                    methods=[method.upper()],
                    name=f"{method}_{route.collection}",
                )

    # ── introspection routes (namespaced to avoid colliding with spec paths) ──
    @app.get("/__sandbox__/state")
    async def _state() -> dict[str, Any]:
        return store.snapshot()

    @app.post("/__sandbox__/reset")
    async def _reset() -> dict[str, str]:
        store.reset()
        return {"status": "reset"}

    return app, store

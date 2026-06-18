"""Unit tests for the spec-driven mock sandbox (Phase 1 verification).

These run with zero external services: no Docker, no API keys, no SYNAPSE backend.
They prove the sandbox supports the stateful, id-chaining behavior that multi-step
and workflow agent tasks rely on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evalkit.sandbox import build_simulator, parse_route

# tests/ -> evaluation/ -> synapse-clone/ ; the fixture lives in the repo.
REPO_ROOT = Path(__file__).resolve().parents[2]
PETSTORE = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"


@pytest.fixture()
def client() -> TestClient:
    app, _store = build_simulator(str(PETSTORE), seed=42)
    return TestClient(app)


# ── route parsing ─────────────────────────────────────────────────────────────

def test_parse_route_collection():
    r = parse_route("/pets")
    assert r.collection == "pets" and not r.item_level and r.parent_collection is None


def test_parse_route_item():
    r = parse_route("/pets/{petId}")
    assert r.collection == "pets" and r.item_level and r.id_param == "petId"


def test_parse_route_nested_collection():
    r = parse_route("/owners/{ownerId}/pets")
    assert r.collection == "pets" and not r.item_level
    assert r.parent_collection == "owners" and r.parent_param == "ownerId"
    assert r.parent_field == "owner_id"


# ── stateful CRUD lifecycle ───────────────────────────────────────────────────

def test_create_returns_id(client: TestClient):
    r = client.post("/pets", json={"name": "Rex", "species": "dog"})
    assert r.status_code == 201
    body = r.json()
    assert body["id"], "create must return an id for downstream chaining"
    assert body["name"] == "Rex"


def test_read_after_create(client: TestClient):
    pet_id = client.post("/pets", json={"name": "Rex", "species": "dog"}).json()["id"]
    r = client.get(f"/pets/{pet_id}")
    assert r.status_code == 200 and r.json()["name"] == "Rex"


def test_update_status_then_filtered_list(client: TestClient):
    # The canonical "create a pet, then mark it sold" workflow task.
    pet_id = client.post("/pets", json={"name": "Rex", "species": "dog"}).json()["id"]
    assert client.put(f"/pets/{pet_id}", json={"status": "sold"}).status_code == 200
    assert client.get(f"/pets/{pet_id}").json()["status"] == "sold"

    sold = client.get("/pets", params={"status": "sold"}).json()
    available = client.get("/pets", params={"status": "available"}).json()
    assert [p["id"] for p in sold] == [pet_id]
    assert available == []


def test_nested_ownership_chaining(client: TestClient):
    # Create owner -> create pet referencing owner -> list that owner's pets.
    owner_id = client.post("/owners", json={"name": "Alice", "email": "a@x.com"}).json()["id"]
    pet_id = client.post("/pets", json={"name": "Rex", "species": "dog", "owner_id": owner_id}).json()["id"]
    client.post("/pets", json={"name": "Other", "species": "cat", "owner_id": "owners_999"})

    owned = client.get(f"/owners/{owner_id}/pets").json()
    assert [p["id"] for p in owned] == [pet_id]


def test_delete_then_404(client: TestClient):
    pet_id = client.post("/pets", json={"name": "Rex", "species": "dog"}).json()["id"]
    assert client.delete(f"/pets/{pet_id}").status_code == 204
    assert client.get(f"/pets/{pet_id}").status_code == 404


def test_get_missing_is_404(client: TestClient):
    assert client.get("/pets/nope").status_code == 404


def test_deterministic_ids_across_fresh_stores():
    # Same call sequence on two fresh sandboxes must yield identical ids.
    def first_id() -> str:
        app, _ = build_simulator(str(PETSTORE), seed=42)
        c = TestClient(app)
        return c.post("/pets", json={"name": "A", "species": "dog"}).json()["id"]

    assert first_id() == first_id() == "pets_1"


def test_state_introspection_endpoint(client: TestClient):
    client.post("/owners", json={"name": "Alice", "email": "a@x.com"})
    state = client.get("/__sandbox__/state").json()
    assert len(state["owners"]) == 1 and state["owners"][0]["name"] == "Alice"

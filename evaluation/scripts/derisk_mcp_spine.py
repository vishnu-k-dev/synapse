#!/usr/bin/env python3
"""De-risk the MCP execution spine — keyless, no SYNAPSE backend required.

Proves the riskiest unknown in the plan end-to-end:

    real server.py.j2  ->  rendered server.py  ->  FastMCP subprocess
                                                         │  (MCP stdio)
    McpToolClient  ──tools/list, tools/call──────────────┘
                                                         │  real httpx
                                                  spec-driven sandbox

If this prints ALL CHECKS PASSED, the live harness only has to swap the *source* of
server.py (template render -> SYNAPSE artifact). Nothing else changes.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from evalkit.mcp_bridge import McpToolClient
from evalkit.sandbox import SandboxServer
from evalkit.server_host import GeneratedServer

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "backend" / "app" / "templates"
PETSTORE = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"


# ── A representative tool context, shaped exactly like SynthesisEngine builds ──
SAMPLE_TOOLS = [
    {
        "name": "list_pets",
        "description": "List pets, optionally filtered by availability status.",
        "method": "GET", "path": "/pets", "has_body": False,
        "required_params": [],
        "optional_params": [{"arg": "limit", "type": "int"}, {"arg": "status", "type": "str"}],
        "query_args": [{"name": "limit", "arg": "limit"}, {"name": "status", "arg": "status"}],
        "body_args": [],
    },
    {
        "name": "get_pet",
        "description": "Fetch a single pet by its id.",
        "method": "GET", "path": "/pets/{pet_id}", "has_body": False,
        "required_params": [{"arg": "pet_id", "type": "str"}],
        "optional_params": [], "query_args": [], "body_args": [],
    },
    {
        "name": "create_pet",
        "description": "Create a new pet in the store.",
        "method": "POST", "path": "/pets", "has_body": True,
        "required_params": [{"arg": "name", "type": "str"}, {"arg": "species", "type": "str"}],
        "optional_params": [{"arg": "breed", "type": "str"}, {"arg": "owner_id", "type": "str"}],
        "query_args": [],
        "body_args": [{"name": "name", "arg": "name"}, {"name": "species", "arg": "species"},
                      {"name": "breed", "arg": "breed"}, {"name": "owner_id", "arg": "owner_id"}],
    },
    {
        "name": "update_pet",
        "description": "Update a pet, e.g. change its status to sold.",
        "method": "PUT", "path": "/pets/{pet_id}", "has_body": True,
        "required_params": [{"arg": "pet_id", "type": "str"}],
        "optional_params": [{"arg": "status", "type": "str"}, {"arg": "name", "type": "str"}],
        "query_args": [],
        "body_args": [{"name": "status", "arg": "status"}, {"name": "name", "arg": "name"}],
    },
]


def render_server(app_name: str, base_url: str, tools: list[dict]) -> str:
    """Render the REAL backend template, byte-identical to what the synthesizer emits."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["py", "ts"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("python_mcp/server.py.j2").render(
        app_name=app_name, base_url=base_url, tools=tools, workflows=[]
    )


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


async def _drive(server: GeneratedServer, sandbox: SandboxServer) -> None:
    async with McpToolClient(server) as mcp:
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        _check("tools/list exposes the 4 generated tools", names ==
               {"list_pets", "get_pet", "create_pet", "update_pet"}, detail=str(sorted(names)))

        # Every tool must advertise a JSON input schema (this is what the agent gets).
        get_pet = next(t for t in tools if t.name == "get_pet")
        _check("get_pet advertises a 'pet_id' parameter schema",
               "pet_id" in (get_pet.input_schema.get("properties") or {}))

        # 1. create -> must return an id (round-trips through generated server to sandbox)
        created = await mcp.call_tool("create_pet", {"name": "Rex", "species": "dog"})
        pet_id = created.get("id")
        _check("create_pet round-trips and returns an id", bool(pet_id), detail=str(pet_id))

        # 2. read back by that id
        fetched = await mcp.call_tool("get_pet", {"pet_id": pet_id})
        _check("get_pet returns the created pet", fetched.get("name") == "Rex")

        # 3. update status -> sold (path + body params together)
        await mcp.call_tool("update_pet", {"pet_id": pet_id, "status": "sold"})
        again = await mcp.call_tool("get_pet", {"pet_id": pet_id})
        _check("update_pet changed status to sold", again.get("status") == "sold")

        # 4. filtered list (query params)
        sold = await mcp.call_tool("list_pets", {"status": "sold"})
        _check("list_pets?status=sold contains the pet",
               isinstance(sold, list) and any(p.get("id") == pet_id for p in sold))

        # 5. the generated server actually mutated sandbox state (not a mock-of-a-mock)
        state = sandbox.store.snapshot()
        _check("sandbox state reflects the call sequence",
               len(state.get("pets", [])) == 1 and state["pets"][0]["status"] == "sold")


def main() -> None:
    print("\nMCP execution-spine de-risk (keyless)\n" + "-" * 50)
    with SandboxServer(str(PETSTORE), seed=42) as sandbox:
        print(f"  sandbox up at {sandbox.base_url}")
        source = render_server("Petstore", sandbox.base_url, SAMPLE_TOOLS)
        _check("template rendered without StrictUndefined errors", "def create_pet(" in source)
        with GeneratedServer(source, api_key="eval-key") as server:
            print(f"  generated server written to {server.path}")
            asyncio.run(_drive(server, sandbox))
    print("-" * 50)
    print("ALL CHECKS PASSED\n")


if __name__ == "__main__":
    sys.exit(main())

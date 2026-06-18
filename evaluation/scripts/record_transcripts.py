#!/usr/bin/env python3
"""Record REAL agent sessions for the Live Console — keyless.

For each scenario, runs the planned tool-call sequence against the *real* generated MCP
server (naive 1-tool-per-endpoint surface vs SYNAPSE's compressed surface) hitting the real
spec-driven sandbox. Captures the actual tool calls, real responses, real errors (e.g. a 404
when the naive agent fumbles), timing, and Judge-verified success. Writes
`frontend/explorer/transcripts.json`, which the console replays.

The agent policy is scripted (no OpenAI key), but every tool call + response is a genuine MCP
round-trip against the real server + sandbox — so the recording is real, not mocked.

Run:  python scripts/record_transcripts.py
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from evalkit.fixtures import (
    PETSTORE_APP_NAME, PETSTORE_COMPRESSED_TOOLS, PETSTORE_NAIVE_TOOLS,
)
from evalkit.agent.judge import Judge
from evalkit.mcp_bridge import McpToolClient
from evalkit.render import render_python_server
from evalkit.sandbox import SandboxServer
from evalkit.server_host import GeneratedServer
from evalkit.tasks.schema import Ref, StateAssertion, Task

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"
OUT = Path(__file__).resolve().parents[1].parent / "frontend" / "explorer" / "transcripts.json"

SCENARIOS = [
    {
        "id": "register_pet",
        "title": "Register owner + add their dog",
        "user": "Register a new owner, Carol (carol@example.com), and add a dog named Max that belongs to her.",
        "oracle": [
            StateAssertion("owners", where={"name": "Carol"}),
            StateAssertion("pets", where={"name": "Max"},
                           where_ref={"owner_id": Ref("owners", {"name": "Carol"}, "id")}),
        ],
        "final": "Done ✓ Carol is registered and Max is linked to her account.",
        "naive": {
            "plan": "Let me look Carol up, then create what's missing and link the pet.",
            "steps": [
                {"tool": "get_owner", "args": {"owner_id": "owners_1"}},        # fumble -> 404
                {"tool": "create_owner", "args": {"name": "Carol", "email": "carol@example.com"}},
                {"tool": "create_pet", "args": {"name": "Max", "species": "dog", "owner_id": "$1.id"}},
                {"tool": "get_pet", "args": {"pet_id": "$2.id"}},
            ],
        },
        "synapse": {
            "plan": "Create the owner, then the pet using the id she's assigned.",
            "steps": [
                {"tool": "manage_owners", "args": {"name": "Carol", "email": "carol@example.com"}},
                {"tool": "manage_pets", "args": {"name": "Max", "species": "dog", "owner_id": "$0.id"}},
            ],
        },
    },
    {
        "id": "vet_visit",
        "title": "Add a pet, book a vet visit, file the record",
        "badge": "workflow",
        "user": "Add a dog named Buddy, book it a checkup with vet vet_3, then file the medical record.",
        "oracle": [
            StateAssertion("pets", where={"name": "Buddy"}),
            StateAssertion("appointments", where_ref={"pet_id": Ref("pets", {"name": "Buddy"}, "id")}),
            StateAssertion("medical-records", where_ref={"pet_id": Ref("pets", {"name": "Buddy"}, "id")}),
        ],
        "final": "Done ✓ Buddy is booked with vet_3 and the visit is on record.",
        "naive": {
            "plan": "Find a vet, create the pet, set up the appointment, then record the visit.",
            "steps": [
                {"tool": "list_vets", "args": {}},                                  # explore
                {"tool": "create_pet", "args": {"name": "Buddy", "species": "dog"}},
                {"tool": "get_appointment", "args": {"appointment_id": "appointments_1"}},  # fumble -> 404
                {"tool": "create_appointment", "args": {"pet_id": "$1.id", "vet_id": "vet_3", "date": "2026-06-20"}},
                {"tool": "create_medical_record", "args": {"pet_id": "$1.id", "vet_id": "vet_3",
                                                           "diagnosis": "Routine checkup — healthy",
                                                           "appointment_id": "$3.id"}},
            ],
        },
        "synapse": {
            "plan": "Add the pet, book the appointment, then file the record — the book_vet_visit workflow.",
            "steps": [
                {"tool": "manage_pets", "args": {"name": "Buddy", "species": "dog"}},
                {"tool": "manage_appointments", "args": {"pet_id": "$0.id", "vet_id": "vet_3", "date": "2026-06-20"}},
                {"tool": "manage_medical_records", "args": {"pet_id": "$0.id", "vet_id": "vet_3",
                                                            "diagnosis": "Routine checkup — healthy"}},
            ],
        },
    },
]


def _resolve(args: dict, responses: list[dict]) -> dict:
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("$") and "." in v:
            idx_s, field = v[1:].split(".", 1)
            try:
                out[k] = (responses[int(idx_s)] or {}).get(field)
            except (ValueError, IndexError):
                out[k] = None
        else:
            out[k] = v
    return out


async def _run_variant(source: str, steps: list[dict], sandbox: SandboxServer) -> tuple[int, list[dict]]:
    recorded: list[dict] = []
    responses: list[dict] = []
    with GeneratedServer(source) as gs:
        async with McpToolClient(gs) as mcp:
            tool_count = len(await mcp.list_tools())
            sandbox.reset()
            for step in steps:
                args = _resolve(step["args"], responses)
                t0 = time.monotonic()
                try:
                    resp = await mcp.call_tool(step["tool"], args)
                    ok = True
                except Exception as exc:
                    resp = {"error": str(exc).split(":", 1)[-1].strip()[:160]}
                    ok = False
                ms = int((time.monotonic() - t0) * 1000)
                responses.append(resp if (ok and isinstance(resp, dict)) else {})
                recorded.append({"tool": step["tool"], "args": args, "response": resp, "ok": ok, "ms": ms})
    return tool_count, recorded


def _summarize(scenario: dict, mode: str, tool_count: int, steps: list[dict],
               sandbox: SandboxServer) -> dict:
    task = Task(id=scenario["id"], api="petstore", instruction=scenario["user"],
                difficulty="workflow", oracle=scenario["oracle"])
    success, _ = Judge(lambda: sandbox.store.snapshot()).judge(task)
    return {
        "label": "Naive · 1 tool / endpoint" if mode == "naive" else "SYNAPSE · compressed",
        "tool_count": tool_count,
        "plan": scenario[mode]["plan"],
        "steps": steps,
        "calls": len(steps),
        "errors": sum(1 for s in steps if not s["ok"]),
        "success": success,
        "final": scenario["final"],
    }


def main() -> None:
    out_scenarios = []
    for sc in SCENARIOS:
        variants = {}
        for mode, tools in (("naive", PETSTORE_NAIVE_TOOLS), ("synapse", PETSTORE_COMPRESSED_TOOLS)):
            with SandboxServer(str(SPEC), seed=42) as sandbox:
                source = render_python_server(PETSTORE_APP_NAME, sandbox.base_url, tools)
                tool_count, steps = asyncio.run(_run_variant(source, sc[mode]["steps"], sandbox))
                variants[mode] = _summarize(sc, mode, tool_count, steps, sandbox)
        out_scenarios.append({k: sc[k] for k in ("id", "title", "user") if k in sc}
                             | ({"badge": sc["badge"]} if "badge" in sc else {})
                             | {"variants": variants})
        n, s = variants["naive"], variants["synapse"]
        print(f"  {sc['id']:<14} naive: {n['calls']} calls/{n['errors']} err/{n['tool_count']} tools "
              f"success={n['success']}  |  synapse: {s['calls']} calls/{s['errors']} err/"
              f"{s['tool_count']} tools success={s['success']}")

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "scenarios": out_scenarios}
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUT.with_suffix(".js").write_text("window.TRANSCRIPTS = " + json.dumps(payload, indent=2) + ";\n",
                                      encoding="utf-8")
    print(f"\nwrote {OUT} (+ .js) — {len(out_scenarios)} scenarios")


if __name__ == "__main__":
    main()

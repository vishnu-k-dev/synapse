"""Regression test for the Phase 6 verification layer (keyless).

Runs the verifier as a subprocess against the fixture-rendered Petstore server and asserts
every tool round-trips (synthesis-correctness == 100%). This is the durable guard for F-1:
revert the template's `-> Any` to `-> dict` and the list-tool check fails here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = EVAL_ROOT / "scripts" / "verify_server.py"


def test_generated_server_synthesis_correctness():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=180,
    )
    combined = proc.stdout + "\n" + proc.stderr
    assert "ALL TOOLS VERIFIED" in proc.stdout, combined
    assert proc.returncode == 0, combined


def test_verify_unit_helpers():
    # Pure-function checks (no subprocess) for the classifier/synthesizer.
    from evalkit.verify import classify, noun_of, synth_args
    assert classify("list_pets") == "list"
    assert classify("create_owner") == "create"
    assert classify("get_pet") == "read"
    assert noun_of("list_pets") == "pet"
    assert noun_of("create_owner") == "owner"
    args = synth_args({"type": "object",
                       "properties": {"name": {"type": "string"}, "pet_id": {"type": "string"}},
                       "required": ["name", "pet_id"]}, id_value="pets_1")
    assert args == {"name": "verify_name", "pet_id": "pets_1"}

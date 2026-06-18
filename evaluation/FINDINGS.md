# SYNAPSE Evaluation — Findings Log

Findings surfaced while building the harness. These are research artifacts: each is a
concrete, reproducible claim about SYNAPSE's behavior that strengthens the writeup
(especially the Phase 5 "synthesis correctness" contribution).

---

## F-1 — Generated MCP servers crash on every list/collection tool

**Severity:** blocking (no API with list endpoints could be evaluated)
**Surfaced by:** `scripts/derisk_mcp_spine.py` (the keyless MCP execution-spine de-risk)
**Status:** fixed in the synthesizer template; recorded here as the original defect.

**What.** The Python MCP template annotated every generated tool `-> dict` and returned
`response.json()` directly. List/collection endpoints (e.g. `GET /pets`) return a JSON
*array*, but the `-> dict` annotation makes FastMCP build a structured-output schema that
**rejects non-dict returns**:

```
ValueError: structured_content must be a dict or None. Got list: [...]
```

This reproduces on both FastMCP 2.14.x and 3.4.x — it is not a version quirk, it is a
template defect. Every read-list and search tool the synthesizer emits is affected.

**Why it went unnoticed.** `scripts/e2e_test.py` only asserts the artifact JSON *exists*
in MinIO — it never executes the generated `server.py`. The servers SYNAPSE ships had
therefore never actually been run. (This is itself a finding: "the artifact is validated
for existence, not executability.")

**Fix.** `backend/app/templates/python_mcp/server.py.j2`: annotate tools `-> Any` instead
of `-> dict` (and import `Any`). FastMCP then emits no structured-output schema and
serializes either shape. One-line change; the de-risk goes fully green after it.

**Phase 5 follow-up.** Generalize into the automated verification layer: assert every
generated tool both (a) exposes a valid MCP schema *and* (b) round-trips a real call
against the sandbox. F-1 is the canonical case that layer must catch.

---

## F-2 — A failed stage leaves the job stuck in `status='running'` forever

**Severity:** medium (operability) — surfaced during the first live run.
**Status:** worked around in the harness; backend fix recommended.

**What.** When a pipeline stage raises (observed: `semantic_engine` hit OpenAI 429
`insufficient_quota`), the stage's `pipeline_event` is correctly marked `failed`, but
`Job.status` is only ever set to `complete` (by the synthesizer) — never to `failed`. So a
failed pipeline appears to "hang": clients polling `job.status` wait until timeout instead
of seeing the failure. The real error is only visible in `pipeline_events` / worker logs.

**Harness workaround.** `SynapseClient.poll_job` now scans `events` and raises immediately
with the failing stage's `error_message`, turning a 600s hang into an instant, actionable error.

**Backend fix (recommended).** In the Celery chain, set `Job.status='failed'` +
`error_message` when a stage raises `StageError` (e.g. in `tasks/pipeline.py` task error
handlers, or a chain error callback). Then `/jobs/{id}` reflects terminal failure directly.


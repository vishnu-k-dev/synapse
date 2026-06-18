# SYNAPSE — Evaluation, Validity & Benchmark: Implementation Plan & Status

This document captures the **complete architecture** of the SYNAPSE evaluation effort and a
clear ledger of **what is implemented** vs **what remains**. It complements the project's
[README](README.md) and the harness's [evaluation/README.md](evaluation/README.md).

## Why this exists

SYNAPSE's thesis — *"semantic tool compression produces a smaller, better tool surface that
improves LLM-agent task success"* — was asserted but **never measured**: there was no
evaluation harness and the README's metric tables were targets, not results. This effort
builds the measurement apparatus (and the novel research extensions around it) to turn the
thesis into evidence, and to make the project stand out at its target venues
(IEEE TENCON / NeurIPS Tool-Use workshop).

---

## System architecture

Two parts: the **SYNAPSE engine** (pre-existing, in `backend/`) and the **evaluation harness**
(new, in `evaluation/`, standalone — imports nothing from `backend/`).

### SYNAPSE engine — 7-stage pipeline (unchanged except one fix)
```
spec ─▶ Discovery ─▶ Extractor ─▶ GraphBuilder ─▶ SemanticEngine ─▶ Compression ─▶ WorkflowDiscovery ─▶ Synthesizer ─▶ MCP server
                                     (Neo4j Capability Graph is the shared contract; Postgres holds job state)
```

### Evaluation harness — measures the agent against the generated artifact
```
GPT-4o agent (function-calling)
        │  picks a tool + args
        ▼
MCP client (evalkit) ──stdio──▶ generated server.py (FastMCP, the REAL artifact)
        ▲                                │ real httpx
        │  tool result                   ▼
        └────────────────  spec-driven mock sandbox  (or a live API)
```
Key design choice: the harness runs the **real generated MCP server** as a subprocess and
drives it over MCP, so it measures what SYNAPSE actually ships — not a reconstruction. This
also gives generated-server verification (Phase 5) nearly for free.

### The experiment: a 2×2 factorial ablation
The independent variable is **tool-set design**. The backend already supports every cell via
`pipeline_config`, so no backend change is needed to run the ablation.

| Condition | `enable_compression` | `enable_workflow_discovery` |
|-----------|:-:|:-:|
| C1 Naive (baseline) | off | off |
| C2 +Compression | on | off |
| C3 +Workflows | off | on |
| C4 Full SYNAPSE | on | on |

Success is decided by a **programmatic oracle** over final sandbox state, including relational
`where_ref` assertions for id-chaining workflow tasks. Metrics: success rate, tool-calls/task,
distinct tools, selection errors, tokens — aggregated with bootstrap 95% CIs and paired
significance tests (McNemar / Wilcoxon).

---

## Status ledger

### ✅ Implemented & verified (22 keyless tests green + frontend MVP)

| Area | Modules | Verification |
|------|---------|--------------|
| Package scaffold + isolated venv | `evaluation/pyproject.toml`, `evalkit/__init__.py` | imports clean |
| Ablation conditions + config | `evalkit/conditions.py`, `evalkit/config.py` | unit-checked |
| **Spec-driven mock sandbox** | `evalkit/sandbox/{state,simulator,runner}.py` | 11 tests (CRUD, id-chaining, nested ownership, deterministic ids, 404s) |
| SYNAPSE HTTP client | `evalkit/synapse_client.py` | drove the live backend; fail-fast on stage errors |
| **MCP execution spine** | `evalkit/{server_host,mcp_bridge,render}.py`, `scripts/derisk_mcp_spine.py` | template→server→MCP→sandbox round-trip (pytest) |
| Tool-schema conversion | `evalkit/toolset.py` | 3 tests |
| **Agent loop + judge + tasks** | `evalkit/agent/{llm,runner,judge,transcript}.py`, `evalkit/tasks/*`, `suites/petstore.yaml` | workflow task solved end-to-end via MockChat (pytest) |
| Metrics / stats / report / store | `evalkit/{stats,report,store}.py` | 4 tests + report files emitted |
| Orchestration + CLI | `evalkit/{experiment,cli}.py`, `run_experiment.py` | offline + live modes wired; `--help` ok |
| Local task fixtures | `evalkit/fixtures.py` | used by offline/keyless runs |
| **Verification layer (Phase 6)** | `evalkit/verify.py`, `scripts/verify_server.py` | per-tool round-trip + synthesis-correctness score; **caught F-1 & F-3** |
| **Frontend explorer MVP (Phase 9)** | `frontend/explorer/` (Cytoscape, vendored) | capability graph + the **19→5 compression animation** + click-to-inspect; verified via preview |

**Live integration proven up to the funded-key boundary:** the full Docker stack came up,
migrations ran, and a real pipeline executed **discovery → extractor → graph_builder** before
hitting the OpenAI account quota limit (see Findings F-2). Infra + harness integration are
confirmed working against the real backend.

### ⬜ Not yet implemented — phased roadmap

Sequential phases, each with a **gate** (a concrete "done when") so they can be tackled one
after another. Phases 0–3 (harness foundations) are complete; the work continues at Phase 4.

#### Phase 4 — First ablation result  ⟵ *immediate next; blocked only on a funded OpenAI key*
Goal: the headline finding — does semantic compression improve agent success?
- [ ] Fund the OpenAI account (embeddings for the pipeline + GPT-4o for the agent).
- [ ] Validation run: `run_experiment.py --mode live --conditions C1_naive,C4_full --repeats 1`.
- [ ] Full 2×2: 4 conditions × 8 tasks × 3 repeats on Petstore.
- [ ] Review `report.md` + `ablation_curve.png`.
- **Gate:** a published ablation table + curve (success rate & tool-calls vs condition) with
  bootstrap CIs and McNemar/Wilcoxon significance.

#### Phase 5 — Faithful baselines & broader coverage
Goal: external validity + cross-API generalization.
- [ ] Generic spec→naive-tools builder so **offline** mode yields a faithful C1 (today it uses a fixture).
- [ ] Real-API adapter (`evalkit/sandbox/real_adapter.py`) — e.g. Petstore live — for hybrid validity.
- [ ] Add 3 APIs (Stripe / GitHub / Jira) + per-API task suites (~40 tasks each).
- [ ] Re-run the 2×2 across all APIs.
- **Gate:** a cross-API ablation table + the same suite passing against one live API.

#### Phase 6 — Research contributions: merge validity + server verification
*Status: the server-verification layer is ✅ built & verified (it already caught F-1 and F-3);
the merge-validity study still needs a funded key.*
Goal: the formal, novel results.
- [ ] Define a **merge-recoverability** metric (can the agent still invoke each merged behavior?).
- [ ] Sweep merge aggressiveness (`compression.py` `_JACCARD_THRESHOLD`, `cluster_selection_epsilon`)
      → plot the **compression-vs-correctness Pareto frontier**.
- [ ] Replace the hard-coded `0.4` threshold with the *measured* criterion.
- [ ] Hypothesis-based **verification layer** generalizing F-1 → per-API synthesis-correctness score.
- **Gate:** a Pareto plot, a principled merge criterion, and a correctness score per API.

#### Phase 7 — Task-aware compression  *(only backend change; highest novelty, highest risk)*
Goal: usage-driven compression beats structural-only.
- [ ] Mine tool co-occurrence + usage frequency from eval transcripts.
- [ ] Feed back as a re-clustering / workflow signal behind a new `PipelineConfig` flag.
- [ ] Ablate usage-aware vs structural-only on the same tasks.
- **Gate:** a measured improvement (or a documented null result).

#### Phase 8 — Benchmark packaging + writeup
Goal: the citable artifact.
- [ ] Package suites + harness + sandbox + leaderboard format as a named benchmark.
- [ ] `make benchmark` entrypoint + paper-ready figures/tables.
- [ ] README / paper framing ("tool-set design as an axis of agent performance").
- **Gate:** a runnable, documented benchmark release.

#### Phase 9 — Frontend: Capability-Graph Explorer (Neo4j-style)  *(see overview below)*
*Status: ◑ static MVP ✅ built & verified (`frontend/explorer/`) — renders the graph + the
19→5 compression animation + click-to-inspect, keyless. Next.js wrap + live-wire pending.*
Goal: the flagship UI + demo magnet (one part of the frontend, alongside upload / catalog / download).
- [ ] Next.js app reading `GET /api/v1/graph/{app_id}`.
- [ ] Force-directed graph (Cytoscape.js or Neo4j NVL); nodes by type, typed edges.
- [ ] The **"92 → 18" compression animation** (Operations collapsing into Tools; Workflow paths lighting up).
- [ ] Click-to-inspect tools/workflows + inline curation via the existing `PATCH` endpoints.
- [ ] **"Run agent on this tool-set"** → triggers the harness, renders metrics inline.
- [ ] Sample capability-graph fixture for a keyless demo.
- **Gate:** the explorer runs (live or from fixture) with the compression animation + run-eval loop.

#### Phase 10 — Platform (Composio-style)  *(see overview below)*
Goal: research engine → usable product.
- [ ] Connected-accounts / auth management (build on the AES-256 `auth_credentials_encrypted` bones).
- [ ] Hosted MCP serving (managed always-on endpoint; stdio + HTTP/SSE).
- [ ] Permission governance — role-scoped tool surfaces (build on `permission_scope`).
- [ ] Tool-call observability — telemetry + dashboards (extend `llm_call_log`).
- [ ] Tool catalog + versioning; triggers/webhooks; framework adapters (OpenAI / LangChain / CrewAI).
- **Gate:** a generated MCP server can be hosted, authed, scoped, and observed through the UI.

### Frontend & platform — toward a Composio-style product (detail for Phases 9–10)

Today SYNAPSE is a backend engine + an evaluation harness. To become a usable product (and a
compelling open-source project) it needs a **frontend** and the **managed-platform** capabilities
that tools like Composio provide. This is the largest unbuilt area — but much of the backend API
and data model needed for it already exists.

**Part A — Capability-Graph Explorer (the Neo4j-style interface).** The flagship UI surface and a
core differentiator: no competitor *shows you the tool surface being designed*. The Capability
Graph already lives in Neo4j and is exposed via `GET /api/v1/graph/{app_id}` (plus `PATCH` to edit
nodes / tools / workflows), so this is a visualization layer over data that already exists. It is
**one part of the frontend** (alongside the spec-upload flow, the tool catalog, and the generated
MCP-server download/deploy panel).
- Interactive, force-directed graph (Neo4j Bloom-style): nodes colored by type — `Entity` /
  `Operation` / `Tool` / `Workflow`; edges for `OWNS` / `OPERATES_ON` / `COMPRESSED_INTO` /
  `PRECEDES` / `PART_OF`.
- **The "92 → 18" compression view** (the demo money-shot): animate raw `Operation` nodes
  collapsing along `COMPRESSED_INTO` edges into `Tool` nodes; `Workflow` nodes light up their
  `PRECEDES` path.
- Click-to-inspect: a `Tool` shows its unified schema, member endpoints, and confidence; a
  `Workflow` shows its ordered steps.
- Human-in-the-loop curation: rename canonical names, reassign entity/action, merge/split tools —
  wired to the existing `PATCH` endpoints.
- **"Run agent on this tool-set"** → triggers the evaluation harness and renders success rate /
  tool-calls inline, closing the design → measure loop in one screen.
- Tech: Next.js + a graph lib (Cytoscape.js — already the intended stack; or Neo4j's **NVL** for a
  true Bloom feel). Can render from a sample capability-graph fixture so it demos with no live pipeline.

**Part B — Managed agent-tooling platform (Composio-style capabilities).** Turn the generated MCP
server from a downloadable file into a hosted, governed service. Several pieces already have
backend bones:
- **Connected accounts & auth management** — OAuth2 / API-key flows, per-user/per-tenant credential
  vaulting, token refresh. *Bones:* `Application.auth_credentials_encrypted` (AES-256) + `auth_type`.
- **Hosted tool serving** — run each generated MCP server as a managed always-on endpoint
  (stdio + HTTP/SSE), instead of a file the user self-hosts.
- **Permission scoping & governance** — role-scoped, least-privilege tool surfaces per agent/user.
  *Bones:* `permission_scope` on `Tool` nodes + permission-aware filtering in the synthesizer.
- **Observability** — per-tool-call telemetry (args, latency, errors, cost) + dashboards.
  *Bones:* `llm_call_log` (extend to a tool-call log).
- **Tool catalog & versioning** — browse/curate tool sets, version and roll back; a multi-app
  integration directory like Composio's.
- **Triggers / webhooks** — event-driven tool invocation.
- **Framework adapters** — one-line export to OpenAI tools / LangChain / CrewAI / Anthropic MCP clients.

**Positioning.** SYNAPSE's edge over Composio-style platforms is the *generation method*: Composio
hand-curates tools per app; SYNAPSE **auto-synthesizes a compressed, workflow-aware tool surface
from any spec** — and, via the harness, can *prove* the surface is better. The platform features
make that method usable; they are the path from "research engine" to "product."

### 🔧 Findings (see [evaluation/FINDINGS.md](evaluation/FINDINGS.md))
- **F-1** (fixed): generated servers crashed on every list/collection tool (`-> dict` + array
  returns) under modern FastMCP — and the repo's e2e test never actually *ran* a generated
  server, so it was latent. Fixed in `backend/app/templates/python_mcp/server.py.j2` (`-> Any`).
- **F-2** (worked around in harness; backend fix recommended): a failed pipeline stage leaves
  `Job.status='running'` forever (only the synthesizer sets `complete`), so failures look like
  hangs. The harness now fails fast on a failed stage event.
- **F-3** (fixed): generated tools crashed on 204 / empty / non-JSON responses (every DELETE)
  because the template unconditionally called `response.json()`. Caught by the Phase 6
  verification layer; fixed in the template (empty → status dict, non-JSON → `{"text": ...}`).

---

## How to run

```bash
cd evaluation
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env            # put OPENAI_API_KEY in the repo-root .env (the harness reads it)

# keyless tests (no backend, no key):
.venv/Scripts/python -m pytest tests -q          # 20 passing

# offline (agent on a local naive surface; needs a funded OpenAI key):
.venv/Scripts/python run_experiment.py --mode offline --api petstore

# full 2x2 ablation (needs the backend up + a funded key):
docker compose -f ../docker-compose.yml up -d postgres neo4j redis minio backend worker
docker compose -f ../docker-compose.yml exec -T backend alembic upgrade head
.venv/Scripts/python run_experiment.py --mode live --api petstore
```
Outputs: `results/<api>/<mode>/report.md`, `summary.csv`, `runs.jsonl`, `ablation_curve.png`.

> **Current blocker:** producing the headline numbers requires an OpenAI account with available
> quota (embeddings for the pipeline + GPT-4o for the agent). Everything else is implemented and
> verified.

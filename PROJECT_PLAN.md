# SYNAPSE — Master Implementation Plan & Status

**Updated:** 2026-06-19 · **main @ `9bed103`**

This is the **single source of truth** for the whole project: the SYNAPSE engine, the evaluation
harness, the verification layer, and the frontend. It consolidates everything now in place and
lays out what remains. For the deep dive on the evaluation effort specifically, see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) (eval-harness focus) and
[`evaluation/FINDINGS.md`](evaluation/FINDINGS.md). For competitive positioning (vs Composio) and
the novel research bets that differentiate SYNAPSE, see
[`RESEARCH_DIRECTIONS.md`](RESEARCH_DIRECTIONS.md).

> **FYP @ BNM Institute of Technology, Bengaluru — AI & ML.** Research target: IEEE TENCON /
> NeurIPS Tool-Use workshop.

---

## 1. What SYNAPSE is

SYNAPSE takes any API spec (OpenAPI 3.x / Postman) and synthesizes an **agent-optimized MCP
server** via a 7-stage semantic pipeline. Its thesis:

> *Semantic tool compression produces a smaller, better tool surface that improves LLM-agent
> task success.*

Previously this was asserted but unmeasured. As of this plan, the engine runs end-to-end **and**
there is a working measurement apparatus that has produced the first real numbers.

---

## 2. System architecture

Three cooperating parts. The evaluation/verification code is standalone — it imports nothing
from `backend/`; it drives the engine over HTTP and executes the **real generated artifact**.

```
                         ┌──────────────────────── SYNAPSE engine (backend/) ───────────────────────┐
  spec ──▶ Discovery ─▶ Extractor ─▶ GraphBuilder ─▶ SemanticEngine ─▶ Compression ─▶ WorkflowDiscovery ─▶ Synthesizer ─▶ MCP server
                         │   Neo4j Capability Graph = shared contract · Postgres = job state · MinIO = artifacts        │
                         └──────────────────────────────────────────────────────────────────────────┘
                                                          ▲ HTTP                         │ artifact (server.py)
                                                          │                              ▼
  ┌──────────── evaluation/ (standalone) ────────────┐    │     ┌─────────── frontend/explorer/ ───────────┐
  │ GPT-4o agent ⇄ MCP client ⇄ generated server.py  │────┘     │ Cytoscape capability-graph explorer +     │
  │           ⇅ real httpx                            │          │ Live Console (recorded transcripts).      │
  │   spec-driven mock sandbox  ·  programmatic oracle│          │ Reads GET /api/v1/graph/{app_id} shape.   │
  │   verification layer (round-trips every tool)     │          └───────────────────────────────────────────┘
  └───────────────────────────────────────────────────┘
```

**The experiment is a 2×2 factorial ablation** over tool-set design, driven entirely by the
backend's existing `PipelineConfig` flags (no backend change to sweep cells):

| Condition | `enable_compression` | `enable_workflow_discovery` |
|-----------|:-:|:-:|
| C1 Naive (baseline) | off | off |
| C2 +Compression | on | off |
| C3 +Workflows | off | on |
| C4 Full SYNAPSE | on | on |

---

## 3. Status at a glance

| Component | Owner | Status | Evidence |
|-----------|-------|--------|----------|
| 7-stage pipeline (backend) | vishnu | ✅ runs E2E | Petstore: 19 ops → 7 entities → 17 tools → 13 workflows → artifact |
| Synthesizer routing | vishnu | ✅ done | generated `server.py` `py_compile`-valid; real method/path/params/body |
| Infra (Docker, migrations) | vishnu | ✅ done | 6 services healthy; `alembic upgrade head` |
| Evaluation harness | revan | ✅ built & validated | 20+ keyless tests; live 2×2 path proven |
| Verification layer (Phase 6) | revan | ✅ built | caught F-1 and F-3 (real round-trips) |
| Frontend explorer (Phase 9) | revan | ◑ static MVP | `frontend/explorer/`, keyless; Next.js wrap pending |
| Funded OpenAI key | — | ✅ available | offline smoke + live C1–C4 ran against it |
| First ablation **validation** | both | ✅ done (today) | C1 vs C4, 1 repeat — see §5 |
| First ablation **full result** | — | ⬜ next | 4 conditions × 8 tasks × 3 repeats (Phase 4 gate) |

---

## 4. What's done & verified

### Backend engine — ✅ complete, runs end-to-end
- All 7 stages implemented; each independently testable; Capability Graph (Neo4j) is the shared
  contract; Postgres holds job state; MinIO holds artifacts.
- **13 runtime bugs** fixed to reach green (build backend, enum double-create, Celery queue
  mismatch, cross-event-loop asyncpg → `NullPool` + per-task dispose, jsonref non-serializable,
  invalid Cypher, Neo4j datetime serialization, presigned-URL host/region, …). Commits
  `76609d6`, `702fb1a`.
- **Synthesizer routing:** Operation nodes carry a `routing` JSON (method, path, params split by
  location); the synthesizer emits a typed FastMCP tool per compressed tool with path-param
  substitution and query/body construction. Output is valid Python (`py_compile`).
- E2E harness `scripts/e2e_test.py` exercises all 7 stages on Petstore.

### Evaluation harness (`evaluation/`) — ✅ built & validated
- Standalone package; spec-driven stateful mock sandbox (any OpenAPI → deterministic REST
  backend); MCP execution spine (render → FastMCP subprocess → MCP client); GPT-4o agent loop;
  programmatic oracle with relational `where_ref` id-chaining; metrics + bootstrap CIs +
  McNemar/Wilcoxon; report/CLI (offline + live). 20+ keyless tests.

### Verification layer (`evalkit/verify.py`, `scripts/verify_server.py`) — ✅ built (Phase 6)
- Asserts every generated tool (a) exposes a valid input schema and (b) **round-trips a real
  call** against the sandbox (create → list/read/update with returned ids → delete). Produces a
  per-API **synthesis-correctness score**. Already caught **F-1** and **F-3** — bugs the existing
  tests never could, because they never executed the generated server.

### Frontend explorer (`frontend/explorer/`) — ◑ static MVP (Phase 9)
- Self-contained Cytoscape.js capability-graph explorer; **no backend / no key**. Renders a real
  Petstore graph and animates operations collapsing into tools + workflows; click-to-inspect.
  Reads the shape of `GET /api/v1/graph/{app_id}` so it can later point at the live API.
- "Live Console" demo with **real recorded transcripts**, naive vs SYNAPSE side-by-side.

---

## 5. First measured results (validation run — 2026-06-19)

Live `--conditions C1_naive,C4_full --repeats 1` on Petstore (8 tasks each):

| Condition | Tools | Success (95% CI) | Tool-calls/task | Avg tokens/task |
|-----------|------:|------------------|----------------:|----------------:|
| C1 Naive | 19 | 62% [25%, 88%] | 2.12 | 5,296 |
| C4 Full SYNAPSE | 17 | 62% [25%, 88%] | 2.12 | 4,858 |

**Honest read.** This validates the *machine*, not yet the *thesis*:
- ✅ The full path works on real money end-to-end for both ablation cells.
- ➕ One directional win: C4's compressed surface used **~8% fewer tokens/task** (efficiency).
- ⚖️ Success tied at 62% — **underpowered** (8 tasks, 1 repeat; significance `n/a`). Expected.
- 🔎 3/8 failures in *both* conditions were the agent **fabricating entity IDs**
  (`404 /pets/pets_1`) instead of chaining from create responses — exactly the id-chaining case
  workflow discovery targets, but invisible at this sample size.

**Cost (measured):** offline smoke ≈ **$0.03**; live validation ≈ **$0.22** (GPT-4o agent
$0.21 + pipeline $0.005). Full 2×2 × 3 repeats extrapolates to **≈ $1–3**.

---

## 6. Remaining roadmap (sequential, gated)

Phases 0–3 (engine + harness foundations) are complete. The work continues here.

### Phase 4 — First ablation result ⟵ *immediate next*
- [x] Fund the OpenAI account · [x] validation run (C1 vs C4, repeats=1)
- [ ] **Full 2×2: 4 conditions × 8 tasks × 3 repeats on Petstore**
- [ ] Review `report.md` + `ablation_curve.png`
- **Gate:** a published ablation table + curve with bootstrap CIs and McNemar/Wilcoxon.

### Phase 5 — Faithful baselines & broader coverage
- [ ] Generic spec→naive-tools builder so **offline** yields a faithful C1 (today uses a fixture).
- [ ] Real-API adapter (`evalkit/sandbox/real_adapter.py`) for hybrid validity.
- [ ] Add 3 APIs (Stripe / GitHub / Jira) + per-API task suites (~40 tasks each); re-run 2×2.
- **Gate:** a cross-API ablation table + the suite passing against one live API.

### Phase 6 — Research contributions: merge validity + verification
*Verification layer is ✅ built (caught F-1, F-3); merge-validity study remains.*
- [ ] **Merge-recoverability** metric (can the agent still invoke each merged behavior?).
- [ ] Sweep merge aggressiveness (`compression.py` `_JACCARD_THRESHOLD`,
      `cluster_selection_epsilon`) → **compression-vs-correctness Pareto frontier**.
- [ ] Replace the hard-coded `0.4` threshold with the *measured* criterion.
- **Gate:** a Pareto plot, a principled merge criterion, and a per-API correctness score.

### Phase 7 — Task-aware compression *(only backend change; highest novelty/risk)*
- [ ] Mine tool co-occurrence + usage frequency from eval transcripts.
- [ ] Feed back as a re-clustering / workflow signal behind a new `PipelineConfig` flag.
- [ ] Ablate usage-aware vs structural-only on the same tasks.
- **Gate:** a measured improvement (or a documented null result).

### Phase 8 — Benchmark packaging + writeup
- [ ] Package suites + harness + sandbox as a named benchmark; `make benchmark`; paper figures.
- **Gate:** a runnable, documented benchmark release.

### Phase 9 — Frontend: Capability-Graph Explorer + product surface
*Static MVP ✅ built.*
- [ ] Next.js 14 + TS app reading live `GET /api/v1/graph/{app_id}`; force-directed graph.
- [ ] The compression animation (Operations → Tools; Workflow paths lighting up).
- [ ] Click-to-inspect + inline curation via existing `PATCH` endpoints.
- [ ] **"Run agent on this tool-set"** → triggers the harness, renders metrics inline.
- **Gate:** the explorer runs (live or fixture) with the compression animation + run-eval loop.

### Phase 10 — Platform (Composio-style)
- [ ] Connected-accounts/auth (build on AES-256 `auth_credentials_encrypted`); hosted MCP serving;
      role-scoped tool surfaces (`permission_scope`); observability (extend `llm_call_log`);
      tool catalog + versioning; framework adapters.
- **Gate:** a generated MCP server can be hosted, authed, scoped, and observed through the UI.

---

## 7. Findings & open follow-ups

| ID | Finding | Status |
|----|---------|--------|
| **F-1** | Generated servers crash on list endpoints (`-> dict` rejects JSON arrays under FastMCP) | ✅ fixed (template `-> Any`) |
| **F-3** | Tools crash on `204` / empty / non-JSON responses (`response.json()` on empty body) | ✅ fixed (template returns status/text) |
| **F-2** | A failed stage leaves `Job.status='running'` forever (only the synthesizer sets a terminal status) | ⬜ **open — backend fix recommended** |

**Open backend follow-ups (not blocking, worth doing):**
1. **F-2:** set `Job.status='failed'` + `error_message` in the Celery error path so `/jobs/{id}`
   reflects terminal failure instead of looking like a 600s hang.
2. **Multi-op tool dispatch:** compressed tools that merge several endpoints currently route via
   the primary member operation. Full multi-endpoint dispatch (pick endpoint by satisfied args)
   is a refinement worth measuring against merge-recoverability (Phase 6).
3. **Workflow rendering:** discovered workflows are scored/visualized but not yet emitted as tools
   in the synthesized server — only Tool nodes are. Render workflows before Phase 7 relies on them.
4. **Agent id-chaining:** the 404-on-fabricated-id failure mode suggests prompt/oracle work and
   more id-chaining tasks (Phase 5) to expose where workflows actually help.

---

## 8. How to run everything

```bash
# 1. Infra + engine (Docker)
cp .env.example .env                         # add OPENAI_API_KEY; CREDENTIAL_ENCRYPTION_KEY via scripts/setup_env.py
docker compose up -d --build                 # postgres, neo4j, redis, minio, backend, worker (no frontend service yet)
docker compose exec backend alembic upgrade head

# 2. Engine E2E (all 7 stages on Petstore)
python scripts/e2e_test.py

# 3. Evaluation (standalone)
cd evaluation && python -m venv .venv && ./.venv/Scripts/python -m pip install -e ".[dev]"
./.venv/Scripts/python -m pytest tests -q                                   # 20+ keyless tests
./.venv/Scripts/python run_experiment.py --mode live --api petstore --conditions all --repeats 3   # full 2×2

# 4. Frontend explorer (keyless)
cd frontend/explorer && python -m http.server 8910                          # open http://localhost:8910
```

---

## 9. Invariants & reproducibility

- **LLMs write only** to `operation.description`, `operation.canonical_name`, `workflow.name`,
  `workflow.description`. Never schemas, paths, or graph structure.
- **All LLM output validated** against Pydantic schemas before any write; **every LLM call logged**
  to `llm_call_log` (the research reproducibility artifact, with `cost_usd`).
- **Prompt-injection defense:** user-controlled content wrapped in `<content>` tags, never raw.
- **`evaluation/` imports nothing from `backend/`** — it measures the shipped artifact, not a
  reconstruction.
- Pinned: agent `gpt-4o-2024-11-20`, `temperature=0`, `seed=42`; pipeline `gpt-4o-mini` +
  `text-embedding-3-small`.

---

## 10. Repository state

- **`main` @ `9bed103`** — engine + harness + verification + frontend MVP all merged.
- PR #1 (eval harness) closed: its commit `eb8a770` landed on main directly.
- Branch `Rev` exists but has nothing beyond main.

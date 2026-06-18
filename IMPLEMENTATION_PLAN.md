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

### ✅ Implemented & verified (20/20 keyless tests green)

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

**Live integration proven up to the funded-key boundary:** the full Docker stack came up,
migrations ran, and a real pipeline executed **discovery → extractor → graph_builder** before
hitting the OpenAI account quota limit (see Findings F-2). Infra + harness integration are
confirmed working against the real backend.

### ⬜ Not yet implemented (remaining roadmap)

| Phase | Work | Notes / blocker |
|-------|------|------|
| **Run** | Execute the 2×2 ablation to produce the headline numbers + curve | **Blocked only on a funded OpenAI key.** One command once available. |
| 1 (rest) | Real-API adapter (`evalkit/sandbox/real_adapter.py`) for hybrid external validity (e.g. Petstore live) | code stub-ready |
| 3.5 | Generic spec→naive-tools builder so **offline** mode produces a faithful C1 baseline | currently offline uses a Petstore fixture |
| 4 | Scale to 3–4 APIs (Stripe/GitHub/Jira) + per-API task suites (~40 tasks each) | needs specs + suites |
| 5 | **Tool-merge validity study**: merge-recoverability metric, sweep `_JACCARD_THRESHOLD` / `cluster_selection_epsilon`, compression-vs-correctness Pareto frontier | the formal research contribution |
| 5 | **Generated-server verification layer** (hypothesis property tests) — generalize F-1 | catches synthesis-correctness bugs |
| 6 | **Task-aware compression**: mine tool co-occurrence from transcripts, feed back as a re-clustering signal | the only backend change; highest novelty |
| 7 | **Benchmark packaging** + paper-ready figures/writeup | the citable artifact |

### 🔧 Findings (see [evaluation/FINDINGS.md](evaluation/FINDINGS.md))
- **F-1** (fixed): generated servers crashed on every list/collection tool (`-> dict` + array
  returns) under modern FastMCP — and the repo's e2e test never actually *ran* a generated
  server, so it was latent. Fixed in `backend/app/templates/python_mcp/server.py.j2` (`-> Any`).
- **F-2** (worked around in harness; backend fix recommended): a failed pipeline stage leaves
  `Job.status='running'` forever (only the synthesizer sets `complete`), so failures look like
  hangs. The harness now fails fast on a failed stage event.

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

# SYNAPSE Evaluation Harness

Measures whether SYNAPSE's semantic tool compression actually improves LLM-agent task
success — the project's core, previously-unproven thesis. Standalone package: it drives
SYNAPSE over HTTP and executes the **real generated MCP server**, importing nothing from
`backend/`.

## How it works

```
GPT-4o agent (function-calling)
        │ picks a tool + args
        ▼
MCP client ──stdio──▶ generated server.py (FastMCP, the real artifact)
        ▲                      │ real httpx
        │ result               ▼
        └──────────  spec-driven mock sandbox  (or a live API)
```

The independent variable is **tool-set design**, swept as a 2×2 ablation (the backend
already supports every cell via `pipeline_config`):

| Condition | compression | workflow discovery |
|-----------|:-:|:-:|
| C1 Naive  | off | off |
| C2 +Compression | on | off |
| C3 +Workflows | off | on |
| C4 Full SYNAPSE | on | on |

Success is decided by a **programmatic oracle** over final sandbox state (incl. relational
`where_ref` assertions for id-chaining workflow tasks). Metrics: success rate, tool-calls/task,
distinct tools, selection errors, tokens — aggregated with bootstrap 95% CIs and paired
significance tests (McNemar / Wilcoxon).

## Setup

```bash
cd evaluation
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env        # then put your OPENAI_API_KEY in .env
```

## Run

**Offline (no backend, only OpenAI key)** — runs the real agent against a local naive tool
surface; approximates the C1 baseline and proves the end-to-end pipeline:

```bash
python run_experiment.py --mode offline --api petstore
```

**Live (full 2×2 ablation)** — needs the SYNAPSE backend up so it can generate each
condition's tool set:

```bash
# from repo root, with Docker Desktop running and a root .env present:
docker compose up -d postgres neo4j redis minio backend worker   # NB: skip 'frontend' (not in repo)
cd backend && alembic upgrade head
# then:
cd ../evaluation && python run_experiment.py --mode live --api petstore
```

Outputs land in `results/<api>/<mode>/`: `report.md`, `summary.csv`, `runs.jsonl`, and
`ablation_curve.png` (if matplotlib is present).

## Tests (all keyless — no backend, no API key)

```bash
.venv/Scripts/python -m pytest tests -q     # 20 tests: sandbox, MCP spine, agent loop, stats
```

Two de-risk scripts double as documentation of the spine:
`scripts/derisk_mcp_spine.py` (template→server→MCP→sandbox) and
`scripts/derisk_agent_loop.py` (full runner→judge on a workflow task).

## Findings

See [`FINDINGS.md`](FINDINGS.md). Notably **F-1**: SYNAPSE's generated servers crashed on
every list/collection tool (annotated `-> dict`, returned arrays) under modern FastMCP, and
had never actually been executed by the repo's e2e test — fixed in the template.

## Status

Phases 0–1 + the MCP execution spine + the agent/judge/metrics/stats/report stack are built
and verified. Remaining: live full-ablation runs (needs key + Docker), real-API adapter
(hybrid validity), merge-validity study, task-aware compression. See the project plan.

# SYNAPSE

**Semantic sYNthesis of Application Protocols for Agent System Enablement**

> A semantic protocol synthesis engine for the AI agent era.

SYNAPSE takes any application's API specification (OpenAPI 3.x / Postman Collection) and produces an agent-optimized MCP server — not by blindly converting endpoints into tools, but by first understanding what the application actually does.

---

## The Problem

Naive MCP generation maps every API endpoint to one tool. A Shopify API with 92 endpoints becomes an MCP server with 92 tools. LLM agents degrade measurably above 40 tools.

| Dimension | Naive MCP | SYNAPSE |
|-----------|-----------|---------|
| Tool Count | 1 per endpoint (often 100+) | Semantic compression to 15–25 |
| Semantic Quality | Raw endpoint name → snake_case | Intent-labeled, entity-scoped |
| Business Logic | None preserved | Workflows detected |
| Security | None | Permission-aware, role-scoped |

## Core Contribution

1. **Capability Graph** — formal directed labeled multigraph `CG = (V, E, L, P, W)` as semantic intermediate representation
2. **Semantic Tool Compression** — embedding (OpenAI) → HDBSCAN clustering → entity-action labeling → schema unification
3. **Workflow Discovery** — detects multi-step business transactions, exposes them as composite MCP tools

## Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI (Python 3.11) |
| Task Queue | Celery + Redis |
| Capability Graph DB | Neo4j 5.x (HNSW vector index) |
| Relational Store | PostgreSQL |
| Embeddings | OpenAI text-embedding-3-small |
| LLM | GPT-4o-mini + Claude Haiku fallback |
| Clustering | HDBSCAN |
| Frontend | Next.js 14 + TypeScript + Cytoscape.js |
| Code Generation | Jinja2 templates |
| Object Storage | MinIO |
| Infra | Docker Compose |

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/vishnu-k-dev/synapse.git
cd synapse
cp .env.example .env          # fill in API keys and passwords

# 2. Start infrastructure
docker compose up -d

# 3. Install backend
cd backend
pip install -e ".[dev]"
alembic upgrade head

# 4. Run
uvicorn app.main:app --reload &
celery -A app.tasks.celery_app worker --loglevel=info
```

## Architecture — 7-Stage Pipeline

```
OpenAPI / Postman Spec
        │
   [Stage 1] Discovery Engine       → RawApplicationModel
        │
   [Stage 2] Capability Extractor   → Entity + Action annotations
        │
   [Stage 3] Graph Builder          → Neo4j Capability Graph
        │
   [Stage 4] Semantic Engine        → LLM enrichment + embeddings
        │
   [Stage 5] Compression Pipeline   → Semantic tool inventory
        │
   [Stage 6] Workflow Discovery     → Composite workflow tools
        │
   [Stage 7] Synthesis Engine       → MCP server (Python / TypeScript)
        │
   Generated MCP Server
```

Each stage is independently testable. The Capability Graph is the shared contract between all stages.

## Research

**FYP @ BNM Institute of Technology, Bengaluru**  
Department of Artificial Intelligence & Machine Learning

**Target venues:** IEEE TENCON 2025 / NeurIPS Tool Use Workshop 2025

**Domain:** Artificial Intelligence & Machine Learning  
**Specialization:** LLM Agent Tool Interface Synthesis & Semantic API Compression

## Evaluation

Benchmarked on 8 public APIs (Stripe, Shopify, GitHub, Jira, Salesforce, Twilio, Petstore, custom Healthcare API) across 50 agent tasks × 4 ablation conditions × 1,600 automated agent runs.

| Metric | Baseline (Naive) | SYNAPSE Target |
|--------|-----------------|----------------|
| Avg Tool Count | 87 | ≤ 22 |
| Agent Task Success Rate | 52% | > 78% |
| Avg Tool Calls / Task | 5.8 | < 2.9 |
| Workflow Coverage | 0% | > 70% |

## License

MIT

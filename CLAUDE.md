# SYNAPSE — Claude Code Context

## What This Is
SYNAPSE is a 7-stage semantic protocol synthesis engine. It takes an OpenAPI 3.x or Postman Collection spec and produces an agent-optimized MCP server by constructing a Capability Graph and running semantic compression + workflow discovery on it.

**FYP @ BNM Institute of Technology, Bengaluru — AI/ML Department**  
**Research target: IEEE TENCON / NeurIPS Workshops 2025**

## Quick Start
```bash
cp .env.example .env        # fill in API keys
docker compose up -d        # starts postgres, neo4j, redis, minio
cd backend && pip install -e ".[dev]"
alembic upgrade head        # run migrations
uvicorn app.main:app --reload
celery -A app.tasks.celery_app worker --loglevel=info
```

## Architecture — The 7 Stages
Each stage is independently testable. All inter-stage communication is through PostgreSQL (job state + ApplicationModel JSON) and Neo4j (Capability Graph). No stage imports another stage.

| # | Stage | Input | Output |
|---|-------|-------|--------|
| 1 | Discovery | spec file/URL | RawApplicationModel (Postgres) |
| 2 | Extractor | RawApplicationModel | AnnotatedApplicationModel (Postgres) |
| 3 | GraphBuilder | AnnotatedApplicationModel | Neo4j graph populated |
| 4 | SemanticEngine | Neo4j graph | Embeddings + normalized names written to graph |
| 5 | Compression | Neo4j embeddings | :Tool nodes created in graph |
| 6 | WorkflowDiscovery | :Tool nodes + :Operation edges | :Workflow nodes + PRECEDES edges |
| 7 | Synthesizer | Complete graph | Rendered code artifact in MinIO |

## Key Invariants
- **LLMs write ONLY to**: `operation.description`, `operation.canonical_name`, `workflow.name`, `workflow.description`. Never schemas, paths, or graph structure.
- **All LLM output is validated** against Pydantic schemas before being written to any store.
- **Every LLM call is logged** to `llm_call_log` table — this is the research reproducibility artifact.
- **Prompt injection defense**: all user-controlled content (API descriptions, path strings) is wrapped in `<content>` XML tags in prompts, never interpolated raw.

## Directory Layout
```
backend/app/
├── core/          # config, logging, security (AES-256)
├── db/            # SQLAlchemy models + async engine
├── graph/         # Neo4j async client + Cypher query library
├── llm/           # LLMClient, PromptRegistry, prompts/*.yaml
├── schemas/       # Pydantic v2: common, pipeline contracts, API schemas
├── pipeline/      # 7 stage implementations (base.py defines Protocol)
├── api/v1/        # FastAPI routers
├── tasks/         # Celery app + pipeline task chain
├── storage/       # MinIO async client
└── templates/     # Jinja2 MCP server templates
```

## Research Notes
- `PipelineConfig.enable_compression` and `.enable_workflow_discovery` are the ablation flags
- Evaluation harness in `evaluation/` is a standalone package — imports nothing from `backend/`
- All GPT-4o agent runs use `model="gpt-4o-2024-11-20"`, `temperature=0`, `seed=42`

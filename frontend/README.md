# SYNAPSE Frontend — Capability Graph Explorer (Phase 9)

A Neo4j-Bloom-style explorer for the SYNAPSE Capability Graph. It visualizes the core idea:
raw API **operations collapsing into a small set of semantic Tools**, with discovered
**Workflows** orchestrating them.

This is the MVP (`explorer/`): a self-contained static app (Cytoscape.js, vendored) that runs
with **no backend and no API key**, rendering a bundled sample capability graph. It reads the
exact shape of `GET /api/v1/graph/{app_id}`, so it can later be pointed at the live API.

## Run it

```bash
cd frontend/explorer
python -m http.server 8910
# open http://localhost:8910
```

- **Raw API** button → the raw surface (19 operations across 5 entities).
- **Run SYNAPSE compression →** → animates the operations collapsing into 5 Tools + 2 Workflows
  ("3.8× compression"). Click any node to inspect it (a Tool shows the endpoints it merged).

## Regenerate the sample graph

```bash
python explorer/make_sample_graph.py    # writes sample-graph.json + sample-graph.js
```
The data is a real, honest Petstore capability graph (19 ops → 5 entity-scoped tools + 2 workflows).

## What it shows

| Node | Meaning |
|------|---------|
| 🔵 Entity | a domain noun (Pet, Owner, …) |
| ⚪ Operation | one raw API endpoint (`GET /pets/{petId}`) |
| 🟢 Tool | a compressed, intent-labeled MCP tool (merges several endpoints) |
| 🟣 Workflow | a discovered multi-step business transaction over tools |

## Productionization (rest of Phase 9)

- Wrap in **Next.js 14 + TypeScript** (the repo's intended stack) and read the live
  `GET /api/v1/graph/{app_id}` instead of the fixture (Neo4j's **NVL** is an option for an even
  closer Bloom feel).
- Inline curation: rename canonical names / reassign entity-action via the existing `PATCH`
  endpoints (`/graph/{app_id}/nodes/{id}`, `/tools/{app_id}/{tool_id}`).
- "Run agent on this tool-set" → trigger the evaluation harness and render success/tool-call
  metrics inline (closes the design → measure loop).

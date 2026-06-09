from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_api_key
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.schemas.api import GraphEdgeSchema, GraphNodeSchema, GraphResponse, UpdateNodeRequest

router = APIRouter(prefix="/graph", tags=["graph"])


def _get_queries() -> GraphQueries:
    return GraphQueries(get_neo4j_client())


@router.get("/{app_id}", response_model=GraphResponse)
async def get_graph(
    app_id: str,
    _: str = Depends(require_api_key),
) -> GraphResponse:
    queries = _get_queries()

    entities = await queries.get_entities(app_id)
    operations = await queries.get_operations(app_id)
    tools = await queries.get_tools(app_id)
    workflows = await queries.get_workflows(app_id)
    stats = await queries.get_graph_stats(app_id)

    nodes: list[GraphNodeSchema] = []
    edges: list[GraphEdgeSchema] = []

    for row in entities:
        e = row.get("e") or {}
        nodes.append(GraphNodeSchema(
            id=e.get("id", ""),
            label=e.get("name", ""),
            node_type="Entity",
            properties={k: v for k, v in e.items() if k != "embedding"},
        ))

    for row in operations:
        o = row.get("o") or {}
        nodes.append(GraphNodeSchema(
            id=o.get("id", ""),
            label=o.get("canonical_name") or o.get("http_path", ""),
            node_type="Operation",
            properties={k: v for k, v in o.items() if k != "embedding"},
        ))
        # OPERATES_ON edge
        if o.get("entity"):
            edges.append(GraphEdgeSchema(
                id=f"op_{o.get('id')}_entity",
                source=o.get("id", ""),
                target=f"{app_id}_{o.get('entity', '')}",
                edge_type="OPERATES_ON",
            ))

    for row in tools:
        t = row.get("t") or {}
        if t.get("is_workflow"):
            continue
        nodes.append(GraphNodeSchema(
            id=t.get("id", ""),
            label=t.get("name", ""),
            node_type="Tool",
            properties={k: v for k, v in t.items() if k not in ("embedding",)},
        ))

    for row in workflows:
        w = row.get("w") or {}
        nodes.append(GraphNodeSchema(
            id=w.get("id", ""),
            label=w.get("name", ""),
            node_type="Workflow",
            properties=dict(w),
        ))

    return GraphResponse(app_id=app_id, nodes=nodes, edges=edges, stats=stats)


@router.patch("/{app_id}/nodes/{node_id}")
async def update_node(
    app_id: str,
    node_id: str,
    request: UpdateNodeRequest,
    _: str = Depends(require_api_key),
) -> dict:
    """Inline edit: update canonical_name, description, entity, or action on an Operation node.
    LLM-writable fields only — schemas and paths are never modified through this endpoint.
    """
    queries = _get_queries()

    if request.canonical_name or request.description:
        await queries.set_operation_name_description(
            operation_id=node_id,
            canonical_name=request.canonical_name or "",
            description=request.description or "",
        )

    return {"updated": node_id}

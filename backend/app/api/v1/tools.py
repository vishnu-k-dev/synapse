from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_api_key
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.schemas.api import ToolSchema, UpdateToolRequest

router = APIRouter(prefix="/tools", tags=["tools"])


def _get_queries() -> GraphQueries:
    return GraphQueries(get_neo4j_client())


@router.get("/{app_id}", response_model=list[ToolSchema])
async def list_tools(
    app_id: str,
    _: str = Depends(require_api_key),
) -> list[ToolSchema]:
    queries = _get_queries()
    tools_raw = await queries.get_tools(app_id)

    tools = []
    for row in tools_raw:
        t = row.get("t") or {}
        op_ids = row.get("operation_ids") or []
        source_count = len(op_ids)
        compression_ratio = source_count if source_count > 0 else 1.0

        tools.append(ToolSchema(
            id=t.get("id", ""),
            name=t.get("name", ""),
            entity=t.get("entity", ""),
            action=t.get("action", ""),
            description=t.get("description", ""),
            source_endpoint_count=source_count,
            compression_ratio=compression_ratio,
            is_workflow=t.get("is_workflow", False),
            confidence=t.get("confidence", 0.0),
            permission_scope=t.get("permission_scope") or [],
            operation_ids=op_ids,
        ))

    return tools


@router.patch("/{app_id}/{tool_id}", response_model=dict)
async def update_tool(
    app_id: str,
    tool_id: str,
    request: UpdateToolRequest,
    _: str = Depends(require_api_key),
) -> dict:
    queries = _get_queries()
    updates: dict[str, object] = {}

    if request.name:
        updates["name"] = request.name
    if request.description:
        updates["description"] = request.description
    if request.permission_scope is not None:
        updates["permission_scope"] = request.permission_scope

    if updates:
        set_clause = ", ".join(f"t.{k} = ${k}" for k in updates)
        await queries._db.run_write_tx(
            f"MATCH (t:Tool {{id: $tool_id}}) SET {set_clause}",
            {"tool_id": tool_id, **updates},
        )

    return {"updated": tool_id}

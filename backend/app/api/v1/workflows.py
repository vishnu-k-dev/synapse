from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.graph.client import get_neo4j_client
from app.graph.queries import GraphQueries
from app.schemas.api import UpdateWorkflowRequest, WorkflowSchema, WorkflowStepSchema

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _get_queries() -> GraphQueries:
    return GraphQueries(get_neo4j_client())


@router.get("/{app_id}", response_model=list[WorkflowSchema])
async def list_workflows(
    app_id: str,
    _: str = Depends(require_api_key),
) -> list[WorkflowSchema]:
    queries = _get_queries()
    workflows_raw = await queries.get_workflows(app_id)

    result = []
    for row in workflows_raw:
        w = row.get("w") or {}
        steps_raw = sorted(row.get("steps") or [], key=lambda s: s.get("step", 0))
        result.append(WorkflowSchema(
            id=w.get("id", ""),
            name=w.get("name", ""),
            description=w.get("description", ""),
            confidence=w.get("confidence", 0.0),
            steps=[
                WorkflowStepSchema(
                    operation_id=s.get("op_id", ""),
                    step_index=s.get("step", 0),
                    required=True,
                )
                for s in steps_raw if s.get("op_id")
            ],
        ))

    return result


@router.patch("/{app_id}/{workflow_id}", response_model=dict)
async def update_workflow(
    app_id: str,
    workflow_id: str,
    request: UpdateWorkflowRequest,
    _: str = Depends(require_api_key),
) -> dict:
    queries = _get_queries()

    if request.name or request.description:
        name = request.name or ""
        desc = request.description or ""
        await queries._db.run_write_tx(
            """
            MATCH (w:Workflow {id: $wf_id})
            SET w.name = CASE WHEN $name <> '' THEN $name ELSE w.name END,
                w.description = CASE WHEN $desc <> '' THEN $desc ELSE w.description END
            """,
            {"wf_id": workflow_id, "name": name, "desc": desc},
        )

    return {"updated": workflow_id}

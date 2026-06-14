"""Cypher query library — all graph queries live here, never inline in pipeline stages."""
from __future__ import annotations

from typing import Any

from app.graph.client import Neo4jClient


class GraphQueries:
    def __init__(self, client: Neo4jClient) -> None:
        self._db = client

    # ── Entity queries ────────────────────────────────────────────────────────

    async def upsert_entity(self, app_id: str, name: str, plural: str, description: str, source_tags: list[str]) -> str:
        result = await self._db.run_write(
            """
            MERGE (e:Entity {app_id: $app_id, name: $name})
            ON CREATE SET
                e.id          = randomUUID(),
                e.plural      = $plural,
                e.description = $description,
                e.source_tags = $source_tags,
                e.created_at  = datetime()
            ON MATCH SET
                e.source_tags = $source_tags
            RETURN e.id AS id
            """,
            {"app_id": app_id, "name": name, "plural": plural,
             "description": description, "source_tags": source_tags},
        )
        return result[0]["id"]

    async def get_entities(self, app_id: str) -> list[dict[str, Any]]:
        return await self._db.run_query(
            "MATCH (e:Entity {app_id: $app_id}) RETURN e",
            {"app_id": app_id},
        )

    # ── Operation queries ─────────────────────────────────────────────────────

    async def create_operation(self, props: dict[str, Any]) -> None:
        await self._db.run_write_tx(
            """
            CREATE (o:Operation {
                id:              $id,
                app_id:          $app_id,
                http_method:     $http_method,
                http_path:       $http_path,
                operation_id:    $operation_id,
                entity:          $entity,
                action:          $action,
                description:     $description,
                param_schema:    $param_schema,
                response_schema: $response_schema,
                routing:         $routing,
                confidence:      $confidence,
                cluster_id:      -1,
                is_compressed:   false,
                is_noise:        false
            })
            WITH o
            OPTIONAL MATCH (e:Entity {app_id: $app_id, name: $entity})
            FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END |
                CREATE (o)-[:OPERATES_ON]->(e)
            )
            """,
            props,
        )

    async def get_operations(self, app_id: str) -> list[dict[str, Any]]:
        return await self._db.run_query(
            "MATCH (o:Operation {app_id: $app_id}) RETURN o ORDER BY o.entity, o.action",
            {"app_id": app_id},
        )

    async def get_operations_with_embeddings(self, app_id: str) -> list[dict[str, Any]]:
        return await self._db.run_query(
            """
            MATCH (o:Operation {app_id: $app_id})
            WHERE o.embedding IS NOT NULL
            RETURN o.id AS id, o.embedding AS embedding, o.entity AS entity,
                   o.action AS action, o.http_method AS http_method,
                   o.http_path AS http_path
            """,
            {"app_id": app_id},
        )

    # ── Relationship queries ───────────────────────────────────────────────────

    async def create_ownership(self, app_id: str, owner: str, owned: str, cardinality: str = "one-to-many") -> None:
        await self._db.run_write_tx(
            """
            MATCH (a:Entity {app_id: $app_id, name: $owner})
            MATCH (b:Entity {app_id: $app_id, name: $owned})
            MERGE (a)-[r:OWNS]->(b)
            ON CREATE SET r.cardinality = $cardinality
            """,
            {"app_id": app_id, "owner": owner, "owned": owned, "cardinality": cardinality},
        )

    # ── Embedding write ───────────────────────────────────────────────────────

    async def set_operation_embedding(self, operation_id: str, embedding: list[float]) -> None:
        await self._db.run_write_tx(
            "MATCH (o:Operation {id: $id}) SET o.embedding = $embedding",
            {"id": operation_id, "embedding": embedding},
        )

    async def set_operation_name_description(self, operation_id: str, canonical_name: str, description: str) -> None:
        await self._db.run_write_tx(
            "MATCH (o:Operation {id: $id}) SET o.canonical_name = $name, o.description = $desc",
            {"id": operation_id, "name": canonical_name, "desc": description},
        )

    # ── Cluster write ─────────────────────────────────────────────────────────

    async def set_cluster_assignments(self, assignments: list[dict[str, Any]]) -> None:
        await self._db.run_write_tx(
            """
            UNWIND $assignments AS a
            MATCH (o:Operation {id: a.id})
            SET o.cluster_id = a.cluster_id, o.is_noise = a.is_noise
            """,
            {"assignments": assignments},
        )

    # ── Tool queries ──────────────────────────────────────────────────────────

    async def create_tool(self, props: dict[str, Any], operation_ids: list[str]) -> None:
        await self._db.run_batch_write([
            (
                """
                CREATE (t:Tool {
                    id:               $id,
                    app_id:           $app_id,
                    name:             $name,
                    description:      $description,
                    unified_schema:   $unified_schema,
                    is_workflow:      false,
                    permission_scope: $permission_scope,
                    confidence:       $confidence
                })
                """,
                props,
            ),
            (
                """
                UNWIND $op_ids AS op_id
                MATCH (o:Operation {id: op_id})
                MATCH (t:Tool {id: $tool_id})
                CREATE (o)-[:COMPRESSED_INTO]->(t)
                SET o.is_compressed = true, o.compressed_into = $tool_id
                """,
                {"op_ids": operation_ids, "tool_id": props["id"]},
            ),
        ])

    async def get_tools(self, app_id: str) -> list[dict[str, Any]]:
        return await self._db.run_query(
            """
            MATCH (t:Tool {app_id: $app_id})
            OPTIONAL MATCH (o:Operation)-[:COMPRESSED_INTO]->(t)
            RETURN t,
                   collect(o.id) AS operation_ids,
                   collect({
                       id:             o.id,
                       method:         o.http_method,
                       path:           o.http_path,
                       routing:        o.routing,
                       action:         o.action,
                       canonical_name: o.canonical_name
                   }) AS operations
            ORDER BY t.name
            """,
            {"app_id": app_id},
        )

    # ── Workflow queries ──────────────────────────────────────────────────────

    async def create_precedes_edge(self, from_id: str, to_id: str, confidence: float, signal: str) -> None:
        await self._db.run_write_tx(
            """
            MATCH (a:Operation {id: $from_id})
            MATCH (b:Operation {id: $to_id})
            MERGE (a)-[r:PRECEDES]->(b)
            ON CREATE SET r.confidence = $confidence, r.signal = $signal
            ON MATCH SET r.confidence = CASE WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END
            """,
            {"from_id": from_id, "to_id": to_id, "confidence": confidence, "signal": signal},
        )

    async def create_workflow(self, props: dict[str, Any], step_operation_ids: list[str]) -> None:
        await self._db.run_batch_write([
            (
                """
                CREATE (w:Workflow {
                    id:          $id,
                    app_id:      $app_id,
                    name:        $name,
                    description: $description,
                    confidence:  $confidence
                })
                """,
                props,
            ),
            (
                """
                UNWIND range(0, size($op_ids)-1) AS idx
                MATCH (o:Operation {id: $op_ids[idx]})
                MATCH (w:Workflow {id: $wf_id})
                CREATE (o)-[:PART_OF {step_index: idx}]->(w)
                """,
                {"op_ids": step_operation_ids, "wf_id": props["id"]},
            ),
        ])

    async def get_workflows(self, app_id: str) -> list[dict[str, Any]]:
        return await self._db.run_query(
            """
            MATCH (w:Workflow {app_id: $app_id})
            OPTIONAL MATCH (o:Operation)-[r:PART_OF]->(w)
            RETURN w, collect({op_id: o.id, step: r.step_index}) AS steps
            ORDER BY w.name
            """,
            {"app_id": app_id},
        )

    async def get_workflow_candidates(self, app_id: str, min_confidence: float = 0.6) -> list[dict[str, Any]]:
        return await self._db.run_query(
            """
            MATCH path = (start:Operation {app_id: $app_id})-[:PRECEDES*1..6]->(end:Operation {app_id: $app_id})
            WHERE NOT ()-[:PRECEDES]->(start) AND NOT (end)-[:PRECEDES]->()
            WITH [n IN nodes(path) | n.id] AS steps,
                 reduce(conf=1.0, r IN relationships(path) | conf * r.confidence) AS chain_conf
            WHERE chain_conf >= $min_confidence AND size(steps) >= 2
            RETURN steps, chain_conf
            ORDER BY chain_conf DESC
            LIMIT 20
            """,
            {"app_id": app_id, "min_confidence": min_confidence},
        )

    # ── Stats / export ─────────────────────────────────────────────────────────

    async def get_graph_stats(self, app_id: str) -> dict[str, int]:
        results = await self._db.run_query(
            """
            RETURN
              COUNT { MATCH (e:Entity {app_id: $app_id}) RETURN e }     AS entities,
              COUNT { MATCH (o:Operation {app_id: $app_id}) RETURN o }  AS operations,
              COUNT { MATCH (t:Tool {app_id: $app_id}) RETURN t }       AS tools,
              COUNT { MATCH (w:Workflow {app_id: $app_id}) RETURN w }   AS workflows
            """,
            {"app_id": app_id},
        )
        if results:
            return {
                "entity_count": results[0].get("entities", 0),
                "operation_count": results[0].get("operations", 0),
                "tool_count": results[0].get("tools", 0),
                "workflow_count": results[0].get("workflows", 0),
            }
        return {"entity_count": 0, "operation_count": 0, "tool_count": 0, "workflow_count": 0}

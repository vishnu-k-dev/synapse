from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver, AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import GraphError

logger = get_logger(__name__)

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=50,
            connection_timeout=15.0,
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


@asynccontextmanager
async def neo4j_session() -> AsyncIterator[AsyncSession]:
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        yield session


class Neo4jClient:
    """Thin async wrapper around Neo4j driver with error translation."""

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with neo4j_session() as s:
            yield s

    async def run_query(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            async with self.session() as session:
                result = await session.run(cypher, parameters or {})
                records = await result.data()
                return records
        except Exception as exc:
            raise GraphError(f"Cypher query failed: {exc}") from exc

    async def run_write(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            async with self.session() as session:
                result = await session.execute_write(
                    lambda tx: tx.run(cypher, parameters or {})
                )
                records = await result.data()
                return records
        except Exception as exc:
            raise GraphError(f"Cypher write failed: {exc}") from exc

    async def run_write_tx(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget write — does not return records."""
        try:
            async with self.session() as session:
                await session.execute_write(
                    lambda tx: tx.run(cypher, parameters or {})
                )
        except Exception as exc:
            raise GraphError(f"Cypher write failed: {exc}") from exc

    async def run_batch_write(
        self,
        operations: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Execute multiple Cypher statements in a single write transaction."""
        try:
            async with self.session() as session:
                async def _tx_fn(tx: Any) -> None:
                    for cypher, params in operations:
                        await tx.run(cypher, params)

                await session.execute_write(_tx_fn)
        except Exception as exc:
            raise GraphError(f"Batch write failed: {exc}") from exc

    async def ensure_indexes(self) -> None:
        """Create vector + constraint indexes — idempotent."""
        statements = [
            # Uniqueness constraints
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT operation_id IF NOT EXISTS FOR (o:Operation) REQUIRE o.id IS UNIQUE",
            "CREATE CONSTRAINT tool_id IF NOT EXISTS FOR (t:Tool) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT workflow_id IF NOT EXISTS FOR (w:Workflow) REQUIRE w.id IS UNIQUE",
            # Text lookup indexes
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.app_id, e.name)",
            "CREATE INDEX operation_app IF NOT EXISTS FOR (o:Operation) ON (o.app_id)",
            # Vector index for embedding similarity search
            """
            CREATE VECTOR INDEX operation_embeddings IF NOT EXISTS
            FOR (o:Operation) ON o.embedding
            OPTIONS {
              indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
              }
            }
            """,
        ]
        for stmt in statements:
            try:
                await self.run_write_tx(stmt)
            except GraphError as exc:
                logger.warning("index_create_warning", error=str(exc))


_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client

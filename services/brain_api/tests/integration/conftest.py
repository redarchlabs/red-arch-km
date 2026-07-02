"""Integration test fixtures: real Qdrant + Neo4j via testcontainers.

These tests verify the brain_sdk against running services rather than mocks.
They require Docker to be available; they skip gracefully if the containers
can't start (e.g. in a CI runner without DinD).
"""

from __future__ import annotations

from collections.abc import Generator

import docker.types
import pytest
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.neo4j import Neo4jContainer

# Qdrant server pinned to match the resolved qdrant-client (1.17.x) so the client's
# major/minor compatibility check passes cleanly. Qdrant requires the client/server
# minor versions differ by at most one.
_QDRANT_IMAGE = "qdrant/qdrant:v1.17.0"
_NEO4J_IMAGE = "neo4j:5.25.1"
_QDRANT_PORT = 6333
# RocksDB opens a large number of segment files as tenant collections accumulate
# over a session-scoped container; the container's default nofile ceiling is easily
# exhausted ("RocksDB open error: ... Too many open files"). Raise it explicitly so
# the integration suite is not gated by the host/container fd default.
_QDRANT_NOFILE = 1048576


@pytest.fixture(scope="session")
def qdrant_container() -> Generator[DockerContainer]:
    """Start a Qdrant container for the test session."""
    container = (
        DockerContainer(_QDRANT_IMAGE)
        .with_exposed_ports(_QDRANT_PORT)
        .with_kwargs(
            ulimits=[docker.types.Ulimit(name="nofile", soft=_QDRANT_NOFILE, hard=_QDRANT_NOFILE)],
        )
    )
    try:
        container.start()
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")
    wait_for_logs(container, "Qdrant HTTP listening", timeout=60)
    yield container
    container.stop()


@pytest.fixture(scope="session")
def neo4j_container() -> Generator[Neo4jContainer]:
    container = Neo4jContainer(image=_NEO4J_IMAGE).with_env("NEO4J_PLUGINS", '["apoc"]')
    try:
        container.start()
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")
    yield container
    container.stop()


@pytest.fixture(scope="session")
def qdrant_url(qdrant_container: DockerContainer) -> str:
    host = qdrant_container.get_container_host_ip()
    port = qdrant_container.get_exposed_port(_QDRANT_PORT)
    return f"http://{host}:{port}"


@pytest.fixture
def vector_store(qdrant_url: str) -> QdrantVectorStore:
    return QdrantVectorStore(url=qdrant_url, dimension=4)


@pytest.fixture
def graph_store(neo4j_container: Neo4jContainer) -> Generator[Neo4jGraphStore]:
    store = Neo4jGraphStore(
        uri=neo4j_container.get_connection_url(),
        user="neo4j",
        password=neo4j_container.password,
    )
    yield store
    store.close()

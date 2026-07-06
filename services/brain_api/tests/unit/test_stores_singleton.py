"""Thread-safety of the lazy-initialized client singletons in ``Stores``.

Callers reach these properties from worker threads (RAG/ingest wrap blocking work
in ``asyncio.to_thread``). Without a lock, two cold requests could both pass the
``is None`` check and build duplicate Neo4j drivers / OpenAI clients — the loser
overwritten and leaked (never ``close()``d). The double-checked lock must ensure
exactly one instance is built and shared.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from brain_api.stores import Stores


def _hammer(get_attr, n: int = 12):  # type: ignore[no-untyped-def]
    """Call ``get_attr()`` from ``n`` threads that all start together."""
    barrier = threading.Barrier(n)

    def worker(_: int):  # type: ignore[no-untyped-def]
        barrier.wait()  # maximise the odds every thread races the cold check
        return get_attr()

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, range(n)))


class TestSingletonUnderConcurrency:
    def test_graph_built_once_and_shared(self) -> None:
        stores = Stores(MagicMock())
        calls = 0
        count_lock = threading.Lock()

        def make_graph(*_args, **_kwargs) -> MagicMock:
            nonlocal calls
            with count_lock:
                calls += 1
            time.sleep(0.05)  # hold the store lock long enough to expose a race
            return MagicMock()

        with patch("brain_api.stores.Neo4jGraphStore", side_effect=make_graph):
            results = _hammer(lambda: stores.graph)

        assert calls == 1  # exactly one driver built despite concurrent cold access
        assert all(r is results[0] for r in results)  # every caller got the same one

    def test_embedder_built_once_and_shared(self) -> None:
        stores = Stores(MagicMock())
        calls = 0
        count_lock = threading.Lock()

        def make_embedder(*_args, **_kwargs) -> MagicMock:
            nonlocal calls
            with count_lock:
                calls += 1
            time.sleep(0.05)
            return MagicMock()

        with patch("brain_api.stores.OpenAIEmbeddingProvider", side_effect=make_embedder):
            results = _hammer(lambda: stores.embedder)

        assert calls == 1
        assert all(r is results[0] for r in results)


def test_close_closes_created_neo4j_clients() -> None:
    stores = Stores(MagicMock())
    graph = MagicMock()
    fact_store = MagicMock()
    with (
        patch("brain_api.stores.Neo4jGraphStore", return_value=graph),
        patch("brain_api.stores.Neo4jFactStore", return_value=fact_store),
        patch("brain_api.stores.PredicateResolver", return_value=MagicMock()),
    ):
        _ = stores.graph
        _ = stores.fact_store

    import asyncio

    asyncio.run(stores.close())
    graph.close.assert_called_once()
    fact_store.close.assert_called_once()

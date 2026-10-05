"""CLI for the fact-graph Document-node backfill (``python -m brain_api.backfill_document_tags``)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from brain_api import backfill_document_tags as cli


@pytest.fixture
def stores() -> MagicMock:
    s = MagicMock()
    s.settings.use_fact_engine = True
    s.close = AsyncMock()
    return s


def test_backfills_each_named_tenant(stores: MagicMock) -> None:
    with (
        patch.object(cli, "_make_stores", return_value=stores),
        patch.object(cli.IngestService, "backfill_document_graph_metadata", autospec=True) as backfill,
    ):
        backfill.return_value = {"documents": 2, "claims_recomputed": 1, "skipped": 0}
        assert cli.main(["--tenant-id", "t1", "--tenant-id", "t2"]) == 0

    assert [c.args[1] for c in backfill.call_args_list] == ["t1", "t2"]
    stores.vector.repair_empty_access_keys.assert_not_called()  # opt-in only


def test_skips_are_reported_but_are_not_failures(stores: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(cli, "_make_stores", return_value=stores),
        patch.object(cli.IngestService, "backfill_document_graph_metadata", autospec=True) as backfill,
    ):
        backfill.side_effect = [
            {"documents": 3, "claims_recomputed": 2, "skipped": 1},
            {"documents": 1, "claims_recomputed": 0, "skipped": 2},
        ]
        assert cli.main(["--tenant-id", "t1", "--tenant-id", "t2"]) == 0

    out = capsys.readouterr().out
    assert "documents=4" in out and "skipped=3" in out and "failed_tenants=0" in out


def test_repair_rewrites_empty_masks_and_reports_counts(stores: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
    stores.vector.repair_empty_access_keys.side_effect = [
        {"scanned": 5, "repaired": 2},
        {"scanned": 0, "repaired": 0},
    ]
    with (
        patch.object(cli, "_make_stores", return_value=stores),
        patch.object(cli.IngestService, "backfill_document_graph_metadata", autospec=True) as backfill,
    ):
        backfill.return_value = {"documents": 0, "claims_recomputed": 0, "skipped": 0}
        assert cli.main(["--tenant-id", "t1", "--tenant-id", "t2", "--repair-empty-masks"]) == 0

    assert [c.args[0] for c in stores.vector.repair_empty_access_keys.call_args_list] == ["t1", "t2"]
    assert "masks_repaired=2" in capsys.readouterr().out


def test_repair_runs_even_with_the_fact_engine_off(stores: MagicMock) -> None:
    """The repair is a vector-store fix; only the graph backfill needs the fact engine."""
    stores.settings.use_fact_engine = False
    stores.vector.repair_empty_access_keys.return_value = {"scanned": 1, "repaired": 1}
    with (
        patch.object(cli, "_make_stores", return_value=stores),
        patch.object(cli.IngestService, "backfill_document_graph_metadata", autospec=True) as backfill,
    ):
        assert cli.main(["--tenant-id", "t1", "--repair-empty-masks"]) == 0
    backfill.assert_not_called()
    stores.vector.repair_empty_access_keys.assert_called_once()


def test_refuses_when_the_fact_engine_is_off(stores: MagicMock) -> None:
    stores.settings.use_fact_engine = False
    with patch.object(cli, "_make_stores", return_value=stores):
        assert cli.main(["--tenant-id", "t1"]) == 1
    stores.close.assert_awaited_once()


def test_a_failing_tenant_does_not_stop_the_rest(stores: MagicMock) -> None:
    with (
        patch.object(cli, "_make_stores", return_value=stores),
        patch.object(cli.IngestService, "backfill_document_graph_metadata", autospec=True) as backfill,
    ):
        backfill.side_effect = [RuntimeError("neo4j down"), {"documents": 1, "claims_recomputed": 0, "skipped": 0}]
        assert cli.main(["--tenant-id", "bad", "--tenant-id", "good"]) == 1

    assert backfill.call_count == 2


def test_tenant_id_is_required() -> None:
    with pytest.raises(SystemExit):
        cli.main([])

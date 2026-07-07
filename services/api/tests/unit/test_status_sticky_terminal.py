"""Terminal ingest states are sticky against the worker status callback.

A long ingest can be run by more than one worker at once — Celery ``acks_late``
redelivers a task that outlives the visibility timeout, so a duplicate copy
keeps polling brain-api and reporting PROGRESS while another copy has already
hit the cancel flag and reported CANCELLED. Without a guard the late PROCESSING
callback overwrote CANCELLED, leaving the UI stuck at "89% processing" under a
log line that said the job was cancelled. ``DocumentRepository.update_status``
now refuses to move a document out of a terminal state; only reprocess re-opens
it (via a separate PENDING write, not this callback).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from api.models.document import ProcessingStatus
from api.repositories.document import DocumentRepository


class _FakeSession:
    """Minimal async session — only ``flush`` is exercised here."""

    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


def _repo_with_doc(doc: SimpleNamespace) -> tuple[DocumentRepository, _FakeSession]:
    session = _FakeSession()
    repo = DocumentRepository(session, uuid.uuid4())

    async def _get(_id: uuid.UUID) -> SimpleNamespace:
        return doc

    repo.get = _get  # type: ignore[method-assign]
    return repo, session


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["CANCELLED", "SUCCESS", "FAILED"])
async def test_terminal_status_not_overwritten_by_processing(terminal: str) -> None:
    doc = SimpleNamespace(
        processing_status=terminal,
        processing_details={"stage": "cancelled"},
    )
    repo, session = _repo_with_doc(doc)

    result = await repo.update_status(uuid.uuid4(), status="PROCESSING", details={"stage": "knowledge", "percent": 89})

    # The straggler callback is a no-op: state and details are preserved.
    assert result is doc
    assert doc.processing_status == terminal
    assert doc.processing_details == {"stage": "cancelled"}
    assert session.flushed is False


@pytest.mark.asyncio
async def test_non_terminal_status_still_advances() -> None:
    doc = SimpleNamespace(processing_status="PROCESSING", processing_details={"stage": "ingesting"})
    repo, session = _repo_with_doc(doc)

    result = await repo.update_status(uuid.uuid4(), status="SUCCESS", details={"chunks": 5, "triplets": 2})

    assert result is doc
    assert doc.processing_status == "SUCCESS"
    assert doc.processing_details == {"chunks": 5, "triplets": 2}
    assert session.flushed is True


@pytest.mark.asyncio
async def test_pending_to_processing_allowed() -> None:
    doc = SimpleNamespace(processing_status="PENDING", processing_details=None)
    repo, session = _repo_with_doc(doc)

    await repo.update_status(uuid.uuid4(), status="PROCESSING", details={"stage": "downloading", "percent": 15})

    assert doc.processing_status == "PROCESSING"
    assert session.flushed is True


def test_terminal_set_is_exactly_the_three_end_states() -> None:
    assert ProcessingStatus.terminal() == {
        ProcessingStatus.SUCCESS,
        ProcessingStatus.FAILED,
        ProcessingStatus.CANCELLED,
    }

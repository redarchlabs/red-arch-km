"""Unit tests for the ProcessingStatus contract.

Guards the enum/value alignment across worker → callback → UI. The worker
writes SUCCESS/FAILED; historically this enum said COMPLETE/ERROR, so status
badges never lit up. These tests fail loudly if the canonical values drift.
"""

from __future__ import annotations

import pytest
from api.models.document import ProcessingStatus
from api.routers.internal import DocumentStatusUpdate
from pydantic import ValidationError


def test_canonical_status_values() -> None:
    assert {s.value for s in ProcessingStatus} == {"PENDING", "PROCESSING", "SUCCESS", "FAILED", "CANCELLED"}


def test_legacy_values_removed() -> None:
    values = {s.value for s in ProcessingStatus}
    assert "COMPLETE" not in values
    assert "ERROR" not in values


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING", "SUCCESS", "FAILED", "CANCELLED"])
def test_status_update_accepts_worker_values(status: str) -> None:
    body = DocumentStatusUpdate(tenant_id="00000000-0000-0000-0000-000000000001", status=status)
    assert body.status == ProcessingStatus(status)


@pytest.mark.parametrize("status", ["COMPLETE", "ERROR", "done", ""])
def test_status_update_rejects_non_canonical(status: str) -> None:
    with pytest.raises(ValidationError):
        DocumentStatusUpdate(tenant_id="00000000-0000-0000-0000-000000000001", status=status)

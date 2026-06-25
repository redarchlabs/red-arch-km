"""Tests for worker retry/error classification."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from worker.tasks.ingest import _is_retryable_http_error


def _make_error(status_code: int) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = ""
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError("error", request=request, response=response)


class TestRetryClassification:
    @pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
    def test_retryable(self, status_code: int) -> None:
        assert _is_retryable_http_error(_make_error(status_code)) is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
    def test_not_retryable(self, status_code: int) -> None:
        assert _is_retryable_http_error(_make_error(status_code)) is False

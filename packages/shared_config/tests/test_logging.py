"""Tests for the JSON logging formatter."""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import patch

import pytest

from shared_config.logging import JSONFormatter, configure_logging


def _capture_record(record: logging.LogRecord) -> dict[str, object]:
    formatter = JSONFormatter()
    return json.loads(formatter.format(record))


class TestJSONFormatter:
    def test_basic_fields(self) -> None:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        payload = _capture_record(record)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "hello world"
        assert "ts" in payload

    def test_extra_fields_preserved(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-123"
        record.status = 200

        payload = _capture_record(record)
        assert payload["request_id"] == "req-123"
        assert payload["status"] == 200

    def test_unserialisable_extra_stringified(self) -> None:
        class Weird:
            def __repr__(self) -> str:
                return "<Weird>"

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hi",
            args=(),
            exc_info=None,
        )
        record.weird = Weird()

        payload = _capture_record(record)
        assert payload["weird"] == "<Weird>"

    def test_exception_included(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = _capture_record(record)
        assert "exception" in payload
        assert "ValueError" in payload["exception"]

    def test_trace_id_injected_when_span_active(self) -> None:
        mock_ctx = type(
            "SC",
            (),
            {"is_valid": True, "trace_id": 0x12345, "span_id": 0xABCDE},
        )()
        mock_span = type("Span", (), {"get_span_context": lambda self: mock_ctx})()
        with patch("shared_config.logging.trace.get_current_span", return_value=mock_span):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="hi",
                args=(),
                exc_info=None,
            )
            payload = _capture_record(record)

        assert payload["trace_id"] == format(0x12345, "032x")
        assert payload["span_id"] == format(0xABCDE, "016x")


class TestConfigureLogging:
    def test_replaces_root_handlers(self) -> None:
        # Install a noise handler first
        noise = logging.StreamHandler()
        logging.getLogger().addHandler(noise)

        configure_logging(level="DEBUG")

        handlers = logging.getLogger().handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0].formatter, JSONFormatter)

    def test_emits_json(self) -> None:
        configure_logging(level="INFO")
        root = logging.getLogger()

        buf = io.StringIO()
        # Swap the stream on the single installed handler
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        original_stream = handler.stream
        handler.stream = buf
        try:
            logging.getLogger("shared_config.tests").info("hello")
        finally:
            handler.stream = original_stream

        line = buf.getvalue().strip()
        assert line, "expected a log line to be emitted"
        parsed = json.loads(line)
        assert parsed["message"] == "hello"

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR"])
    def test_honors_log_level(self, level: str) -> None:
        configure_logging(level=level)
        assert logging.getLogger().level == getattr(logging, level)

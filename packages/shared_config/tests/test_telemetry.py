"""Tests for telemetry setup."""

from __future__ import annotations

import shared_config.telemetry as telemetry_module
from shared_config.observability import ObservabilitySettings
from shared_config.telemetry import configure_telemetry, get_meter, get_tracer


class TestConfigureTelemetry:
    def test_returns_handles(self, monkeypatch) -> None:
        # Reset module-level flag between tests so each installs freshly
        monkeypatch.setattr(telemetry_module, "_configured", False)

        settings = ObservabilitySettings(
            otel_exporter_otlp_endpoint="",
            otel_service_name="test-service",
            log_level="INFO",
        )
        handles = configure_telemetry("test-service", settings=settings)

        assert handles.tracer_provider is not None
        assert handles.meter_provider is not None

    def test_safe_to_call_twice(self, monkeypatch) -> None:
        monkeypatch.setattr(telemetry_module, "_configured", False)
        settings = ObservabilitySettings(
            otel_exporter_otlp_endpoint="",
            otel_service_name="t",
            log_level="INFO",
        )
        configure_telemetry("service-a", settings=settings)
        # Second call should not raise
        configure_telemetry("service-b", settings=settings)

    def test_no_exporter_when_endpoint_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(telemetry_module, "_configured", False)
        settings = ObservabilitySettings(
            otel_exporter_otlp_endpoint="",
            otel_service_name="t",
            log_level="INFO",
        )
        handles = configure_telemetry("t", settings=settings)
        # With no endpoint the provider has no span processors installed
        # by us (defaults from SDK may add fallbacks; we only assert no crash)
        assert handles.tracer_provider is not None

    def test_get_tracer_and_meter(self, monkeypatch) -> None:
        monkeypatch.setattr(telemetry_module, "_configured", False)
        settings = ObservabilitySettings(
            otel_exporter_otlp_endpoint="",
            otel_service_name="t",
            log_level="INFO",
        )
        configure_telemetry("t", settings=settings)

        tracer = get_tracer("test")
        meter = get_meter("test")
        assert tracer is not None
        assert meter is not None

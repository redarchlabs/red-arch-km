"""OpenTelemetry tracer + meter provider setup shared across services.

The configuration is idempotent: calling `configure_telemetry` twice is safe
(subsequent calls are no-ops). If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset,
telemetry is configured with a no-op exporter so services still run locally
without an OTLP collector.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from shared_config.observability import ObservabilitySettings

logger = logging.getLogger(__name__)

_configured = False


@dataclass(frozen=True, slots=True)
class TelemetryHandles:
    """Return value from configure_telemetry, mostly for testing visibility."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider


def configure_telemetry(
    service_name: str,
    settings: ObservabilitySettings | None = None,
) -> TelemetryHandles:
    """Configure OTel tracer + meter providers.

    Safe to call multiple times — only the first call installs providers.
    """
    global _configured
    settings = settings or ObservabilitySettings()  # type: ignore[call-arg]

    resource = Resource.create({SERVICE_NAME: service_name})
    tracer_provider = TracerProvider(resource=resource)
    meter_provider_readers: list[PeriodicExportingMetricReader] = []

    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if endpoint:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        meter_provider_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True),
                export_interval_millis=10_000,
            )
        )
        logger.info("Telemetry exporter enabled: %s (service=%s)", endpoint, service_name)
    else:
        logger.info("No OTEL endpoint configured; telemetry runs local-only")

    meter_provider = MeterProvider(resource=resource, metric_readers=meter_provider_readers)

    if not _configured:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        _configured = True

    return TelemetryHandles(tracer_provider=tracer_provider, meter_provider=meter_provider)


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    return metrics.get_meter(name)

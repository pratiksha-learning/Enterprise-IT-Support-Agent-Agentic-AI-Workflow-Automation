"""OpenTelemetry setup plus an in-process metrics registry.

* Traces/metrics are exported to Azure Monitor (Application Insights) when
  APPLICATIONINSIGHTS_CONNECTION_STRING is set and the exporter package is installed,
  to an OTLP collector when OTEL_EXPORTER_OTLP_ENDPOINT is set, or to the console
  when OTEL_CONSOLE_EXPORT=true. Otherwise spans are created but not exported.
* ``MetricsRegistry`` keeps a small in-memory copy of the key business metrics so the
  local admin dashboard (/api/v1/admin/metrics) works without any cloud backend.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)
_INSTRUMENT_NAME = "it_support_agent"
_configured = False


def setup_telemetry(settings) -> str:
    """Configure global tracer/meter providers. Returns the exporter name used."""
    global _configured
    if _configured:
        return "already-configured"
    resource = Resource.create({"service.name": settings.otel_service_name,
                                "deployment.environment": settings.environment})
    tracer_provider = TracerProvider(resource=resource)
    readers = []
    exporter_name = "none"

    if settings.applicationinsights_connection_string is not None:
        try:
            from azure.monitor.opentelemetry.exporter import (  # type: ignore
                AzureMonitorMetricExporter,
                AzureMonitorTraceExporter,
            )

            conn = settings.applicationinsights_connection_string.get_secret_value()
            tracer_provider.add_span_processor(BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=conn)))
            readers.append(PeriodicExportingMetricReader(AzureMonitorMetricExporter(connection_string=conn)))
            exporter_name = "azure-monitor"
        except ImportError:
            logger.warning("azure-monitor-opentelemetry-exporter not installed; App Insights export disabled")
    elif settings.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter  # type: ignore
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore

            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
            exporter_name = "otlp"
        except ImportError:
            logger.warning("opentelemetry-exporter-otlp not installed; OTLP export disabled")
    elif settings.otel_console_export:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000))
        exporter_name = "console"

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))
    _configured = True
    return exporter_name


def get_tracer():
    return trace.get_tracer(_INSTRUMENT_NAME)


class MetricsRegistry:
    """Thread-safe business metrics: mirrors values into OpenTelemetry instruments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        self._latency: dict[str, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
        meter = metrics.get_meter(_INSTRUMENT_NAME)
        self._otel_counters = {
            "tool_invocations": meter.create_counter("agent.tool.invocations", description="Tool invocations by tool/outcome"),
            "agent_executions": meter.create_counter("agent.executions", description="Agent turns by intent/outcome"),
            "approvals_completed": meter.create_counter("approvals.completed", description="Approval decisions by outcome"),
            "errors": meter.create_counter("app.errors", description="Errors by code"),
            "retries": meter.create_counter("downstream.retries", description="Downstream retries by operation"),
            "http_requests": meter.create_counter("api.requests", description="API requests by route/status"),
        }
        self._otel_hist = {
            "http_latency_ms": meter.create_histogram("api.latency", unit="ms"),
            "tool_latency_ms": meter.create_histogram("agent.tool.latency", unit="ms"),
            "agent_latency_ms": meter.create_histogram("agent.execution.latency", unit="ms"),
            "approval_duration_s": meter.create_histogram("approvals.duration", unit="s"),
        }

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._counters[name][key] += value
        if name in self._otel_counters:
            self._otel_counters[name].add(value, labels)

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            bucket = self._latency[name][key]
            bucket.append(value)
            if len(bucket) > 2000:
                del bucket[:1000]
        if name in self._otel_hist:
            self._otel_hist[name].record(value, labels)

    def snapshot(self) -> dict:
        def pct(values: list[float], p: float) -> float:
            s = sorted(values)
            return round(s[min(len(s) - 1, int(p * len(s)))], 2) if s else 0.0

        with self._lock:
            counters = {
                name: [{"labels": dict(k), "value": v} for k, v in series.items()]
                for name, series in self._counters.items()
            }
            hist = {
                name: [
                    {"labels": dict(k), "count": len(v), "avg": round(sum(v) / len(v), 2) if v else 0,
                     "p50": pct(v, 0.5), "p95": pct(v, 0.95)}
                    for k, v in series.items()
                ]
                for name, series in self._latency.items()
            }
        return {"counters": counters, "histograms": hist}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._latency.clear()


metrics_registry = MetricsRegistry()


@contextmanager
def traced(name: str, **attributes) -> Iterator:
    """Create a span; mark it as error when the block raises."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            if v is not None:
                span.set_attribute(k, v)
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            span.record_exception(exc)
            raise


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self.start) * 1000
        return False

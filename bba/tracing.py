"""Optional local OpenTelemetry tracing for BBA and Google ADK."""

from __future__ import annotations

import atexit
import functools
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


TRACE_ENDPOINT_ENV = "BBA_OTLP_TRACES_ENDPOINT"
_SERVICE_VERSION = "0.13.0"
_lock = threading.Lock()
_state: dict[str, Any] = {
    "enabled": False,
    "endpoint": None,
    "exporter": "none",
    "reason": "BBA_OTLP_TRACES_ENDPOINT is not set",
    "content_captured": False,
}
_configured = False

_ALLOWED_ATTRIBUTE_PREFIXES = (
    "bba.",
    "gen_ai.operation.name",
    "gen_ai.agent.name",
    "gen_ai.system",
    "gen_ai.request.model",
    "gen_ai.request.max_tokens",
    "gen_ai.request.top_p",
    "gen_ai.response.model",
    "gen_ai.response.finish_reasons",
    "gen_ai.usage.",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "error.type",
)


def _safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    def permitted(key: str) -> bool:
        return key.startswith("bba.") or key in _ALLOWED_ATTRIBUTE_PREFIXES or any(
            key.startswith(prefix)
            for prefix in _ALLOWED_ATTRIBUTE_PREFIXES
            if prefix.endswith(".")
        )

    safe = {
        str(key): value
        for key, value in (attributes or {}).items()
        if permitted(str(key))
    }
    error_type = safe.get("error.type")
    if error_type is not None and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", str(error_type)
    ):
        safe["error.type"] = "error"
    return safe


class _PrivacyFilterExporter(SpanExporter):
    """Remove content, events, links, and descriptive fields before export."""

    def __init__(self, delegate: SpanExporter) -> None:
        self.delegate = delegate

    def export(self, spans):
        sanitized = tuple(ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=_safe_attributes(span.attributes),
            events=(),
            links=(),
            kind=span.kind,
            status=Status(
                StatusCode.ERROR
                if span.status.status_code is StatusCode.ERROR
                else span.status.status_code
            ),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        ) for span in spans)
        try:
            return self.delegate.export(sanitized)
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self, *args, **kwargs):
        return self.delegate.shutdown(*args, **kwargs)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.delegate.force_flush(timeout_millis)


def _loopback_endpoint(value: str) -> str:
    endpoint = value.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("the BBA OTLP trace endpoint must use HTTP on the local host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("the BBA OTLP trace endpoint cannot contain credentials or options")
    if parsed.path in {"", "/"}:
        endpoint = endpoint.rstrip("/") + "/v1/traces"
    return endpoint


def configure_tracing(
    endpoint: Optional[str] = None,
    *,
    exporter: Optional[SpanExporter] = None,
) -> Mapping[str, Any]:
    """Configure one process-wide ADK and BBA trace provider.

    Export is off unless ``BBA_OTLP_TRACES_ENDPOINT`` is set. The endpoint must
    be on the local host. Import or exporter failures disable tracing and never
    stop an epoch.
    """

    global _configured
    with _lock:
        if _configured:
            return tracing_status()
        selected = endpoint if endpoint is not None else os.getenv(TRACE_ENDPOINT_ENV, "")
        if not selected and exporter is None:
            _configured = True
            return tracing_status()
        try:
            safe_endpoint = _loopback_endpoint(selected) if selected else "in-memory-test"
            current_provider = trace.get_tracer_provider()
            if not isinstance(current_provider, trace.ProxyTracerProvider):
                raise RuntimeError(
                    "a process-wide OpenTelemetry provider is already configured"
                )
            selected_exporter = exporter
            if selected_exporter is None:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                selected_exporter = OTLPSpanExporter(
                    endpoint=safe_endpoint,
                    headers={},
                    timeout=2,
                )
            provider = TracerProvider(resource=Resource.create({
                "service.name": "benchbenchagent",
                "service.version": _SERVICE_VERSION,
                "service.instance.id": f"local-{os.getpid()}",
            }))
            provider.add_span_processor(BatchSpanProcessor(
                _PrivacyFilterExporter(selected_exporter),
                schedule_delay_millis=1000,
                export_timeout_millis=3000,
            ))
            trace.set_tracer_provider(provider)
            if trace.get_tracer_provider() is not provider:
                raise RuntimeError("BBA could not install its OpenTelemetry provider")
            atexit.register(provider.shutdown)
            _state.update({
                "enabled": True,
                "endpoint": safe_endpoint,
                "exporter": type(selected_exporter).__name__,
                "reason": None,
                "content_captured": False,
            })
        except Exception as exc:  # tracing must never stop benchmark work
            _state.update({
                "enabled": False,
                "endpoint": None,
                "exporter": "none",
                "reason": f"{type(exc).__name__}: {exc}",
                "content_captured": False,
            })
        _configured = True
        return tracing_status()


def tracing_status() -> dict[str, Any]:
    """Return privacy-safe process tracing status."""

    return dict(_state)


def _attribute_value(value: Any) -> Any:
    if isinstance(value, (str, bool, int, float)):
        return value
    if value is None:
        return ""
    if isinstance(value, (tuple, list)) and all(
        isinstance(item, (str, bool, int, float)) for item in value
    ):
        return tuple(value)
    return str(value)


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[Mapping[str, Any]] = None,
    *,
    tracer: Any = None,
) -> Iterator[Any]:
    """Create one content-free span without changing application behavior."""

    selected = tracer or trace.get_tracer("bba.controller", _SERVICE_VERSION)
    safe_attributes = {
        str(key): _attribute_value(value)
        for key, value in (attributes or {}).items()
    }
    with selected.start_as_current_span(
        name,
        attributes=safe_attributes,
        record_exception=True,
        set_status_on_exception=True,
    ) as span:
        yield span


def traced(name: str, attributes: Any = None):
    """Trace a synchronous operation with optional attribute construction."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            values = attributes(*args, **kwargs) if callable(attributes) else attributes
            with trace_span(name, values):
                return function(*args, **kwargs)

        return wrapped

    return decorate

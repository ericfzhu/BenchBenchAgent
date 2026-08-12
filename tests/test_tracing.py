"""Privacy and behavior tests for optional OpenTelemetry tracing."""

from __future__ import annotations

import contextvars
import unittest
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExportResult,
    SpanExporter,
)

import bba.tracing as tracing
from bba.scheduler import BoundedScheduler


class CaptureExporter(SpanExporter):
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self, *args, **kwargs):
        return None

    def force_flush(self, timeout_millis=30000):
        return True


class TestTracing(unittest.TestCase):
    def test_loopback_endpoint_rules(self):
        self.assertEqual(
            tracing._loopback_endpoint("http://127.0.0.1:4318"),
            "http://127.0.0.1:4318/v1/traces",
        )
        self.assertEqual(
            tracing._loopback_endpoint("http://localhost:4318/custom"),
            "http://localhost:4318/custom",
        )
        for endpoint in (
            "https://collector.example/v1/traces",
            "http://user:secret@localhost:4318",
            "grpc://localhost:4317",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                tracing._loopback_endpoint(endpoint)

    def test_privacy_exporter_removes_content_events_and_links(self):
        capture = CaptureExporter()
        exporter = tracing._PrivacyFilterExporter(capture)
        span = ReadableSpan(
            "tool",
            attributes={
                "bba.epoch.id": "epoch-one",
                "gen_ai.tool.name": "read_bundle_file",
                "gen_ai.tool.name.untrusted_suffix": "private value",
                "gen_ai.tool.description": "private description",
                "gcp.vertex.agent.tool_call_args": '{"secret":"value"}',
                "gcp.vertex.agent.tool_response": '{"gold":"value"}',
                "exception.message": "private error text",
                "error.type": "ValueError",
            },
            events=(object(),),
            links=(object(),),
            status=Status(StatusCode.ERROR, "private status description"),
        )
        self.assertEqual(exporter.export((span,)), SpanExportResult.SUCCESS)
        saved = capture.spans[0]
        self.assertEqual(saved.attributes, {
            "bba.epoch.id": "epoch-one",
            "gen_ai.tool.name": "read_bundle_file",
            "error.type": "ValueError",
        })
        self.assertEqual(saved.events, ())
        self.assertEqual(saved.links, ())
        self.assertEqual(saved.status.status_code, StatusCode.ERROR)
        self.assertIsNone(saved.status.description)

    def test_untrusted_error_type_is_replaced(self):
        self.assertEqual(
            tracing._safe_attributes({"error.type": "secret value from a response"}),
            {"error.type": "error"},
        )

    def test_nested_bba_spans_keep_their_parent_relationship(self):
        capture = CaptureExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(capture))
        tracer = provider.get_tracer("bba-test")
        with tracing.trace_span("bba.epoch.public", tracer=tracer):
            with tracing.trace_span("bba.creator.round", tracer=tracer):
                pass
        by_name = {span.name: span for span in capture.spans}
        parent = by_name["bba.epoch.public"]
        child = by_name["bba.creator.round"]
        self.assertEqual(child.parent.span_id, parent.context.span_id)

    def test_exporter_failure_does_not_escape(self):
        class BrokenExporter(CaptureExporter):
            def export(self, spans):
                raise RuntimeError("collector is unavailable")

        exporter = tracing._PrivacyFilterExporter(BrokenExporter())
        result = exporter.export((ReadableSpan("bba.epoch.public"),))
        self.assertEqual(result, SpanExportResult.FAILURE)

    def test_missing_or_invalid_export_configuration_is_nonfatal(self):
        with patch.dict(tracing._state, {
            "enabled": False,
            "endpoint": None,
            "exporter": "none",
            "reason": "not configured",
            "content_captured": False,
        }, clear=True), patch.object(tracing, "_configured", False):
            status = tracing.configure_tracing("https://remote.example/v1/traces")
        self.assertFalse(status["enabled"])
        self.assertIn("local host", status["reason"])

    def test_local_export_configuration_installs_one_provider(self):
        installed = []

        def get_provider():
            return installed[-1] if installed else trace.ProxyTracerProvider()

        with patch.dict(tracing._state, {
            "enabled": False,
            "endpoint": None,
            "exporter": "none",
            "reason": "not configured",
            "content_captured": False,
        }, clear=True), patch.object(tracing, "_configured", False), patch.object(
            tracing.trace, "get_tracer_provider", side_effect=get_provider
        ), patch.object(
            tracing.trace, "set_tracer_provider", side_effect=installed.append
        ), patch.object(tracing.atexit, "register"):
            status = tracing.configure_tracing(
                "http://127.0.0.1:4318", exporter=CaptureExporter()
            )
            installed[0].shutdown()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["endpoint"], "http://127.0.0.1:4318/v1/traces")

    def test_scheduler_propagates_trace_context_to_workers(self):
        marker = contextvars.ContextVar("marker", default="missing")
        marker.set("parent")
        result = BoundedScheduler(workers=1).map((
            ("work", "publisher", lambda: marker.get()),
        ))
        self.assertEqual(result, {"work": "parent"})


if __name__ == "__main__":
    unittest.main()

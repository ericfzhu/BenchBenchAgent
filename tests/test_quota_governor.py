"""Quota discovery and rolling-governor tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bba.catalog import SERVERLESS_COHORT
from bba.quota import (
    ModelQuotaPolicy,
    QuotaSnapshot,
    QuotaGovernor,
    VertexQuotaDiscovery,
    quota_base_model,
)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, _credentials, payload):
        self.payload = payload

    def get(self, _url, params=None, timeout=None):
        return FakeResponse(self.payload)


class StaticDiscovery:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def discover(self, _identities):
        return self.snapshot


class TestQuotaDiscovery(unittest.TestCase):
    def test_grok_project_limits_are_scaled_to_two_thirds(self):
        grok = next(item for item in SERVERLESS_COHORT if item.model == "grok-4.3")
        metrics = []
        for metric, value in (
            ("aiplatform.googleapis.com/global_generate_content_requests_per_minute_per_project_per_base_model", 6),
            ("aiplatform.googleapis.com/global_generate_content_input_tokens_per_minute_per_base_model", 40000),
            ("aiplatform.googleapis.com/global_generate_content_output_tokens_per_minute_per_base_model", 12000),
        ):
            metrics.append({
                "metric": metric,
                "consumerQuotaLimits": [{
                    "quotaBuckets": [{
                        "effectiveLimit": str(value),
                        "dimensions": {"base_model": "grok-4.3"},
                    }],
                }],
            })
        discovery = VertexQuotaDiscovery(
            "example-project",
            credentials_loader=lambda **_kwargs: (object(), "example-project"),
            session_factory=lambda credentials: FakeSession(
                credentials, {"metrics": metrics}
            ),
        )
        policy = discovery.discover((grok,)).policies[grok.artifact_id]
        self.assertEqual(policy.effective_requests_per_minute, 4)
        self.assertEqual(policy.effective_input_tokens_per_minute, 26666)
        self.assertEqual(policy.effective_output_tokens_per_minute, 8000)
        self.assertAlmostEqual(policy.minimum_spacing_seconds, 15.0)

    def test_new_claude_versions_share_lineage_buckets(self):
        opus5 = next(item for item in SERVERLESS_COHORT if item.model == "claude-opus-5")
        opus48 = next(item for item in SERVERLESS_COHORT if item.model == "claude-opus-4-8")
        opus47 = next(item for item in SERVERLESS_COHORT if item.model == "claude-opus-4-7")
        self.assertEqual(quota_base_model(opus5), "anthropic-claude-opus")
        self.assertEqual(quota_base_model(opus48), "anthropic-claude-opus")
        self.assertEqual(quota_base_model(opus47), "anthropic-claude-opus-4-7")


class TestQuotaGovernor(unittest.TestCase):
    def test_fixed_bucket_spaces_requests_and_reconciles_reserved_tokens(self):
        grok = next(item for item in SERVERLESS_COHORT if item.model == "grok-4.3")
        policy = ModelQuotaPolicy(
            project="example-project",
            location="global",
            bucket="grok-4.3",
            mode="fixed",
            provider_requests_per_minute=6,
            provider_input_tokens_per_minute=40000,
            provider_output_tokens_per_minute=12000,
            effective_requests_per_minute=4,
            effective_input_tokens_per_minute=26666,
            effective_output_tokens_per_minute=8000,
            utilization=2 / 3,
            metrics={},
        )
        snapshot = QuotaSnapshot(
            project="example-project",
            location="global",
            discovered_at="2026-08-16T00:00:00+00:00",
            utilization=2 / 3,
            policies={grok.artifact_id: policy},
        )
        now = [1000.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as temporary:
            governor = QuotaGovernor(
                Path(temporary),
                "example-project",
                discovery=StaticDiscovery(snapshot),
                clock=lambda: now[0],
                sleeper=sleep,
            )
            first = governor.acquire(grok, 1000, 2000)
            governor.reconcile(first, 800, 500)
            second = governor.acquire(grok, 1000, 2000)
            self.assertTrue(second)
            self.assertGreaterEqual(sum(sleeps), 15.0)
            status = governor.status((grok,))["models"][0]
            self.assertEqual(status["rolling_usage"]["requests"], 2)
            self.assertEqual(status["rolling_usage"]["input_tokens"], 1800)
            self.assertEqual(status["rolling_usage"]["output_tokens"], 2500)


if __name__ == "__main__":
    unittest.main()

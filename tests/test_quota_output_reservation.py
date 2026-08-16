"""Per-request output allocation tests for fixed Vertex quota buckets."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bba.catalog import SERVERLESS_COHORT
from bba.quota import ModelQuotaPolicy, QuotaSnapshot
from bba.quota_project import QuotaGovernor


class StaticDiscovery:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def discover(self, _identities):
        return self.snapshot


class TestPerRequestOutputReservation(unittest.TestCase):
    def _setup(self):
        grok = next(
            item for item in SERVERLESS_COHORT
            if item.model == "grok-4.3"
        )
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
        return grok, snapshot

    def test_four_calls_fit_the_safe_minute_at_the_fair_share(self):
        grok, snapshot = self._setup()
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
            starts = []
            caps = []
            for _ in range(4):
                lease = governor.acquire_model_call(
                    grok,
                    estimated_input_tokens=1000,
                    requested_output_tokens=16000,
                )
                starts.append(now[0])
                caps.append(lease.output_cap)
                governor.reconcile(
                    lease.lease_id,
                    input_tokens=800,
                    output_tokens=2000,
                )

            self.assertEqual(caps, [2000, 2000, 2000, 2000])
            self.assertEqual(starts, [1000.0, 1015.0, 1030.0, 1045.0])
            self.assertEqual(sum(sleeps), 45.0)
            status = governor.status((grok,))["models"][0]
            self.assertEqual(
                status["bba"]["nominal_output_tokens_per_request"],
                2000,
            )
            self.assertEqual(status["rolling_usage"]["requests"], 4)
            self.assertEqual(status["rolling_usage"]["output_tokens"], 8000)

    def test_unused_headroom_is_reallocated_to_later_calls(self):
        grok, snapshot = self._setup()
        now = [2000.0]

        with tempfile.TemporaryDirectory() as temporary:
            governor = QuotaGovernor(
                Path(temporary),
                "example-project",
                discovery=StaticDiscovery(snapshot),
                clock=lambda: now[0],
                sleeper=lambda seconds: now.__setitem__(
                    0,
                    now[0] + seconds,
                ),
            )
            first = governor.acquire_model_call(grok, 1000, 16000)
            self.assertEqual(first.output_cap, 2000)
            governor.reconcile(first.lease_id, 800, 500)

            second = governor.acquire_model_call(grok, 1000, 16000)
            self.assertEqual(second.output_cap, 2500)


if __name__ == "__main__":
    unittest.main()

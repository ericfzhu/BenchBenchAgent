"""Conservative pricing and USD budget tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bba.catalog import SERVERLESS_COHORT
from bba.pricing import PriceCatalog
from bba.state import LocalStateStore, STATE_SCHEMA_VERSION


class TestCostControls(unittest.TestCase):
    def test_price_catalog_covers_the_frozen_cohort_and_retries(self):
        manifest = SimpleNamespace(
            cohort=SERVERLESS_COHORT,
            thresholds=SimpleNamespace(
                rounds=3,
                solver_repetitions=3,
            ),
            retry_policy=SimpleNamespace(max_attempts=3),
            budget=SimpleNamespace(
                max_tokens=16000,
                max_estimated_cost_usd=5000.0,
            ),
        )
        estimate = PriceCatalog().estimate(manifest)
        self.assertTrue(estimate["complete"])
        self.assertTrue(estimate["within_hard_limit"])
        expected_runs = (3 + 3 * len(SERVERLESS_COHORT) * 3 + 3 * len(SERVERLESS_COHORT)) * 3
        self.assertTrue(
            all(row["runs"] == expected_runs for row in estimate["models"].values())
        )

    def test_state_store_enforces_the_frozen_usd_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            manifest = SimpleNamespace(
                epoch_id="cost-limit-test",
                digest="d" * 64,
                budget=SimpleNamespace(max_estimated_cost_usd=0.01),
            )
            state.register_epoch(manifest)
            limits = {
                "calls": 100,
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            }
            with self.assertRaisesRegex(RuntimeError, "estimated-cost"):
                state.reserve_inference(
                    manifest.epoch_id,
                    "expensive-call",
                    1,
                    1000,
                    1000,
                    limits,
                )
            self.assertEqual(state.inference_cost_usd(manifest.epoch_id), 0.0)
            self.assertEqual(STATE_SCHEMA_VERSION, 3)

    def test_reconciliation_releases_unused_reserved_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            manifest = SimpleNamespace(
                epoch_id="cost-reconcile-test",
                digest="e" * 64,
                budget=SimpleNamespace(max_estimated_cost_usd=10.0),
            )
            state.register_epoch(manifest)
            limits = {
                "calls": 100,
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            }
            state.reserve_inference(
                manifest.epoch_id,
                "call-one",
                4,
                10_000,
                10_000,
                limits,
            )
            reserved = state.inference_cost_usd(manifest.epoch_id)
            state.reconcile_inference(
                manifest.epoch_id,
                "call-one",
                1,
                1000,
                1000,
            )
            self.assertLess(state.inference_cost_usd(manifest.epoch_id), reserved)


if __name__ == "__main__":
    unittest.main()

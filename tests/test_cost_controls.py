"""Model-specific pricing estimates and USD budget tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bba.catalog import SERVERLESS_COHORT
from bba.pricing import PriceCatalog, model_cost_context
from bba.protocol import ModelIdentity
from bba.state import LocalStateStore, STATE_SCHEMA_VERSION
from bba.tournament import _SessionBudgetState


def estimate_manifest(limit: float = 500.0):
    return SimpleNamespace(
        cohort=SERVERLESS_COHORT,
        thresholds=SimpleNamespace(
            rounds=3,
            solver_repetitions=3,
        ),
        retry_policy=SimpleNamespace(max_attempts=3),
        budget=SimpleNamespace(
            max_tokens=16_000,
            max_llm_calls=64,
            max_estimated_cost_usd=limit,
        ),
    )


def state_manifest(epoch_id: str, limit: float):
    return SimpleNamespace(
        epoch_id=epoch_id,
        digest=(epoch_id[0] if epoch_id else "d") * 64,
        budget=SimpleNamespace(max_estimated_cost_usd=limit),
    )


class TestCostControls(unittest.TestCase):
    def test_uncached_planning_and_stress_estimates_fit_the_target(self):
        estimate = PriceCatalog().estimate(estimate_manifest())
        self.assertTrue(estimate["complete"])
        self.assertTrue(estimate["within_hard_limit"])
        self.assertAlmostEqual(
            estimate["planning_provider_cost_usd"],
            113.55705,
            places=5,
        )
        self.assertAlmostEqual(
            estimate["planning_estimate_usd"],
            227.1141,
            places=4,
        )
        self.assertAlmostEqual(
            estimate["stress_provider_cost_usd"],
            247.24,
            places=2,
        )
        self.assertAlmostEqual(
            estimate["stress_estimate_usd"],
            494.47,
            places=2,
        )
        self.assertEqual(
            estimate["conservative_estimate_usd"],
            estimate["stress_estimate_usd"],
        )
        self.assertGreater(estimate["maximum_first_attempt_usd"], 500.0)
        self.assertGreater(
            estimate["maximum_retry_envelope_usd"],
            estimate["maximum_first_attempt_usd"],
        )

        cohort = len(SERVERLESS_COHORT)
        first_attempt_runs = 3 + 3 * cohort * 3 + 3 * cohort
        retry_envelope_runs = first_attempt_runs * 3
        self.assertTrue(
            all(
                row["first_attempt_runs"] == first_attempt_runs
                and row["runs"] == retry_envelope_runs
                for row in estimate["models"].values()
            )
        )
        first = estimate["models"][SERVERLESS_COHORT[0].artifact_id]
        self.assertNotIn("effective_input_tokens", first)

    def test_model_specific_runtime_cost_ignores_stale_worst_route(self):
        grok = ModelIdentity(
            "xai",
            "grok-4.3",
            "grok",
            "litellm:vertex_ai/xai/grok-4.3",
        )
        opus = ModelIdentity(
            "anthropic",
            "claude-opus-5",
            "claude",
            "claude:claude-opus-5",
        )
        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            manifest = state_manifest("model-cost-test", 10.0)
            state.register_epoch(manifest)
            proxy = _SessionBudgetState(
                state,
                SimpleNamespace(max_tokens=10_000, max_llm_calls=1),
                (grok, opus),
            )
            limits = {
                "calls": 100,
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            }
            proxy.reserve_inference(
                manifest.epoch_id,
                f"cell--{grok.artifact_id}--attempt-1",
                1,
                10_000,
                1_000,
                limits,
            )
            after_grok = state.inference_cost_usd(manifest.epoch_id)
            proxy.reserve_inference(
                manifest.epoch_id,
                f"cell--{opus.artifact_id}--attempt-1",
                1,
                10_000,
                1_000,
                limits,
            )
            opus_increment = (
                state.inference_cost_usd(manifest.epoch_id) - after_grok
            )
            self.assertAlmostEqual(after_grok, 0.03, places=6)
            self.assertAlmostEqual(opus_increment, 0.15, places=6)
            self.assertGreater(opus_increment, after_grok)

    def test_unattributed_direct_state_calls_remain_fail_safe(self):
        catalog = PriceCatalog()
        exact = catalog.conservative_cost(
            10_000,
            1_000,
            model="claude-opus-5",
        )
        unattributed = catalog.conservative_cost(10_000, 1_000)
        self.assertGreaterEqual(unattributed, exact)

    def test_trusted_fixture_context_is_explicitly_cost_exempt(self):
        catalog = PriceCatalog()
        with model_cost_context("fixture-model", cost_exempt=True):
            self.assertEqual(
                catalog.conservative_cost(100_000, 10_000),
                0.0,
            )

    def test_state_store_enforces_the_frozen_usd_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            manifest = state_manifest("cost-limit-test", 0.01)
            state.register_epoch(manifest)
            limits = {
                "calls": 100,
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            }
            with model_cost_context("claude-opus-5"):
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
            manifest = state_manifest("cost-reconcile-test", 10.0)
            state.register_epoch(manifest)
            limits = {
                "calls": 100,
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            }
            with model_cost_context("claude-opus-5"):
                state.reserve_inference(
                    manifest.epoch_id,
                    "call-one",
                    4,
                    10_000,
                    10_000,
                    limits,
                )
            reserved = state.inference_cost_usd(manifest.epoch_id)
            with model_cost_context("claude-opus-5"):
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

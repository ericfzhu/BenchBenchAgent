"""Consistent ADK session-token and model-attributed reservation tests."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from bba.adk_runtime import _ObservabilityPlugin
from bba.pricing import PriceCatalog, current_cost_attribution
from bba.protocol import ModelIdentity
from bba.session_budget import agent_session_budget
from bba.tournament import _SessionBudgetState


class RecordingState:
    def __init__(self) -> None:
        self.reservations = []
        self.reconciliations = []

    def reserve_inference(self, *args) -> None:
        attribution = current_cost_attribution()
        cost = PriceCatalog().conservative_cost(args[3], args[4])
        self.reservations.append((args, attribution, cost))

    def reconcile_inference(self, *args) -> None:
        self.reconciliations.append((args, current_cost_attribution()))


class TestSessionBudget(unittest.TestCase):
    def setUp(self):
        self.identity = ModelIdentity(
            "xai",
            "grok-4.3",
            "grok",
            "litellm:vertex_ai/xai/grok-4.3",
        )

    def test_default_contract_is_1024k_input_and_16k_output(self):
        value = agent_session_budget(
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64)
        )
        self.assertEqual(value.max_output_tokens_per_call, 16_000)
        self.assertEqual(value.max_session_input_tokens, 1_024_000)
        self.assertEqual(value.max_session_output_tokens, 16_000)
        self.assertEqual(value.max_llm_calls, 64)

    def test_controller_reservation_matches_runtime_session_contract(self):
        state = RecordingState()
        budget = SimpleNamespace(max_tokens=16_000, max_llm_calls=64)
        proxy = _SessionBudgetState(state, budget, (self.identity,))
        limits = {
            "calls": 150_000,
            "input_tokens": 150_000_000,
            "output_tokens": 40_000_000,
        }
        reservation_id = f"cell--{self.identity.artifact_id}--attempt-1"
        proxy.reserve_inference(
            "epoch",
            reservation_id,
            64,
            16_000,
            16_000,
            limits,
        )
        args, attribution, cost = state.reservations[0]
        self.assertEqual(args[2:5], (64, 1_024_000, 16_000))
        self.assertEqual(attribution.model, "grok-4.3")
        self.assertFalse(attribution.cost_exempt)
        self.assertAlmostEqual(cost, 2.64, places=6)

        proxy.reconcile_inference(
            "epoch",
            reservation_id,
            2,
            20_000,
            2_000,
        )
        self.assertEqual(
            state.reconciliations[0][1].model,
            "grok-4.3",
        )

    def test_hidden_scaffold_identity_maps_to_the_same_price_key(self):
        state = RecordingState()
        proxy = _SessionBudgetState(
            state,
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64),
            (self.identity,),
        )
        hidden_artifact = (
            "gcp__xai__grok-4.3__explicit-supported-settings-v1__sealed-v1-abc"
        )
        proxy.reserve_inference(
            "epoch",
            f"snapshot--{hidden_artifact}--r0--attempt-1",
            64,
            16_000,
            16_000,
            {
                "calls": 150_000,
                "input_tokens": 150_000_000,
                "output_tokens": 40_000_000,
            },
        )
        self.assertEqual(
            state.reservations[0][1].model,
            "grok-4.3",
        )

    def test_trusted_fixture_controller_marks_unknown_models_cost_exempt(self):
        fixture = ModelIdentity(
            "test",
            "fixture-model",
            "test",
            "litellm:test/fixture-model",
        )
        state = RecordingState()
        proxy = _SessionBudgetState(
            state,
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64),
            (fixture,),
            cost_exempt=True,
        )
        proxy.reserve_inference(
            "epoch",
            f"cell--{fixture.artifact_id}--attempt-1",
            64,
            16_000,
            16_000,
            {
                "calls": 150_000,
                "input_tokens": 150_000_000,
                "output_tokens": 40_000_000,
            },
        )
        _, attribution, cost = state.reservations[0]
        self.assertEqual(attribution.model, "fixture-model")
        self.assertTrue(attribution.cost_exempt)
        self.assertEqual(cost, 0.0)

    def test_unknown_reservation_identity_fails_closed(self):
        state = RecordingState()
        proxy = _SessionBudgetState(
            state,
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64),
            (self.identity,),
        )
        with self.assertRaisesRegex(RuntimeError, "attribute inference"):
            proxy.reserve_inference(
                "epoch",
                "unknown-attempt",
                64,
                16_000,
                16_000,
                {
                    "calls": 150_000,
                    "input_tokens": 150_000_000,
                    "output_tokens": 40_000_000,
                },
            )

    def test_plugin_caps_later_turn_by_remaining_session_output(self):
        identity = ModelIdentity(
            "google",
            "scripted",
            "test",
            "gemini:scripted",
        )
        plugin = _ObservabilityPlugin(
            10,
            {},
            epoch_id="session-budget",
            role="solver",
            identity=identity,
            session_id="session",
            invocation_id="invocation",
            store=None,
            max_llm_calls=64,
        )
        first_request = SimpleNamespace(config=None)
        asyncio.run(
            plugin.before_model_callback(
                callback_context=None,
                llm_request=first_request,
            )
        )
        self.assertEqual(first_request.config.max_output_tokens, 10)
        first_response = SimpleNamespace(
            model_version=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=40,
                candidates_token_count=6,
                total_token_count=46,
            ),
        )
        asyncio.run(
            plugin.after_model_callback(
                callback_context=None,
                llm_response=first_response,
            )
        )

        second_request = SimpleNamespace(config=None)
        asyncio.run(
            plugin.before_model_callback(
                callback_context=None,
                llm_request=second_request,
            )
        )
        self.assertEqual(second_request.config.max_output_tokens, 4)

    def test_plugin_enforces_cumulative_input_independently(self):
        identity = ModelIdentity(
            "google",
            "scripted",
            "test",
            "gemini:scripted",
        )
        plugin = _ObservabilityPlugin(
            10,
            {},
            epoch_id="session-input-budget",
            role="solver",
            identity=identity,
            session_id="session",
            invocation_id="invocation",
            store=None,
            max_llm_calls=64,
        )
        request = SimpleNamespace(config=None)
        asyncio.run(
            plugin.before_model_callback(
                callback_context=None,
                llm_request=request,
            )
        )
        response = SimpleNamespace(
            model_version=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=641,
                candidates_token_count=1,
                total_token_count=642,
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "session token budget"):
            asyncio.run(
                plugin.after_model_callback(
                    callback_context=None,
                    llm_response=response,
                )
            )


if __name__ == "__main__":
    unittest.main()

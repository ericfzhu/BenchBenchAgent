"""Consistent ADK session-token and SQLite reservation tests."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from bba.adk_runtime import _ObservabilityPlugin
from bba.protocol import ModelIdentity
from bba.session_budget import agent_session_budget
from bba.tournament import _SessionBudgetState


class RecordingState:
    def __init__(self) -> None:
        self.reservations = []

    def reserve_inference(self, *args) -> None:
        self.reservations.append(args)


class TestSessionBudget(unittest.TestCase):
    def test_default_contract_is_128k_input_and_16k_output(self):
        value = agent_session_budget(
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64)
        )
        self.assertEqual(value.max_output_tokens_per_call, 16_000)
        self.assertEqual(value.max_session_input_tokens, 128_000)
        self.assertEqual(value.max_session_output_tokens, 16_000)
        self.assertEqual(value.max_llm_calls, 64)

    def test_controller_reservation_matches_runtime_session_contract(self):
        state = RecordingState()
        budget = SimpleNamespace(max_tokens=16_000, max_llm_calls=64)
        proxy = _SessionBudgetState(state, budget)
        limits = {
            "calls": 150_000,
            "input_tokens": 150_000_000,
            "output_tokens": 40_000_000,
        }
        proxy.reserve_inference(
            "epoch",
            "attempt",
            64,
            16_000,
            16_000,
            limits,
        )
        reservation = state.reservations[0]
        self.assertEqual(reservation[2:5], (64, 128_000, 16_000))

    def test_non_agent_reservation_is_not_rewritten(self):
        state = RecordingState()
        proxy = _SessionBudgetState(
            state,
            SimpleNamespace(max_tokens=16_000, max_llm_calls=64),
        )
        proxy.reserve_inference(
            "epoch",
            "manual",
            1,
            100,
            50,
            {"calls": 10, "input_tokens": 1000, "output_tokens": 1000},
        )
        self.assertEqual(state.reservations[0][2:5], (1, 100, 50))

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
                prompt_token_count=81,
                candidates_token_count=1,
                total_token_count=82,
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

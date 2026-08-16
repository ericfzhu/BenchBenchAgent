"""ADK model-call quota hook tests."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bba.adk_runtime import _ObservabilityPlugin
from bba.catalog import SERVERLESS_COHORT


class FakeGovernor:
    def __init__(self):
        self.acquired = []
        self.reconciled = []

    def estimate_input_tokens(self, _request): return 1234
    def output_cap(self, _identity, requested): return min(requested, 2000)
    def acquire(self, identity, estimated_input, output):
        self.acquired.append((identity.model, estimated_input, output)); return "lease-1"
    def reconcile(self, lease, input_tokens, output_tokens):
        self.reconciled.append((lease, input_tokens, output_tokens))
    def fail(self, _lease, _error): pass


class TestQuotaAdkHook(unittest.TestCase):
    def test_each_underlying_model_call_acquires_and_reconciles_quota(self):
        identity = next(item for item in SERVERLESS_COHORT if item.model == "grok-4.3")
        governor = FakeGovernor()
        store = SimpleNamespace(evidence_root=Path(tempfile.gettempdir()))
        with patch("bba.adk_runtime.QuotaGovernor.from_environment", return_value=governor):
            plugin = _ObservabilityPlugin(
                16000, identity.behavior_settings,
                epoch_id="quota-hook", role="solver", identity=identity,
                session_id="session", invocation_id="invocation", store=store,
            )
        request = SimpleNamespace(config=None, model_dump=lambda **_kwargs: {"contents": []})
        asyncio.run(plugin.before_model_callback(callback_context=None, llm_request=request))
        self.assertEqual(request.config.max_output_tokens, 2000)
        self.assertEqual(governor.acquired, [("grok-4.3", 1234, 2000)])
        response = SimpleNamespace(
            model_version=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=900,
                candidates_token_count=400,
                total_token_count=1300,
            ),
        )
        asyncio.run(plugin.after_model_callback(callback_context=None, llm_response=response))
        self.assertEqual(governor.reconciled, [("lease-1", 900, 400)])


if __name__ == "__main__":
    unittest.main()

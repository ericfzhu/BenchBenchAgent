"""Frozen catalog routes cannot silently run without quota governance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bba.adk_runtime import _ObservabilityPlugin
from bba.catalog import SERVERLESS_COHORT
from bba.protocol import ModelIdentity


class TestQuotaFailClosed(unittest.TestCase):
    def test_catalog_route_propagates_governor_initialization_failure(self):
        identity = next(
            item for item in SERVERLESS_COHORT if item.model == "grok-4.3"
        )
        store = SimpleNamespace(evidence_root=Path(tempfile.gettempdir()))
        with patch(
            "bba.adk_runtime.QuotaGovernor.from_environment",
            side_effect=RuntimeError("quota database is unavailable"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "quota database is unavailable",
            ):
                _ObservabilityPlugin(
                    16_000,
                    identity.behavior_settings,
                    epoch_id="quota-fail-closed",
                    role="solver",
                    identity=identity,
                    session_id="session",
                    invocation_id="invocation",
                    store=store,
                    max_llm_calls=64,
                )

    def test_catalog_route_requires_an_evidence_root(self):
        identity = SERVERLESS_COHORT[0]
        with self.assertRaisesRegex(
            RuntimeError,
            "evidence-backed quota governor",
        ):
            _ObservabilityPlugin(
                16_000,
                identity.behavior_settings,
                epoch_id="quota-no-store",
                role="solver",
                identity=identity,
                session_id="session",
                invocation_id="invocation",
                store=None,
                max_llm_calls=64,
            )

    def test_non_catalog_deterministic_model_does_not_need_live_quota(self):
        identity = ModelIdentity(
            "google",
            "scripted-test-model",
            "test",
            "gemini:scripted-test-model",
        )
        store = SimpleNamespace(evidence_root=Path(tempfile.gettempdir()))
        with patch(
            "bba.adk_runtime.QuotaGovernor.from_environment"
        ) as factory:
            plugin = _ObservabilityPlugin(
                100,
                {},
                epoch_id="quota-offline-test",
                role="solver",
                identity=identity,
                session_id="session",
                invocation_id="invocation",
                store=store,
                max_llm_calls=4,
            )
        factory.assert_not_called()
        self.assertIsNone(plugin._quota_governor)


if __name__ == "__main__":
    unittest.main()

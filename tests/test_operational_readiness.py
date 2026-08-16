"""Operational readiness checks for Ubuntu and Google Cloud execution."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bba.cli
from bba.adk_runtime import _validate_debrief_coverage
from bba.evidence import EvidenceStore
from bba.gcp import configure_gcp_environment, discover_gcp_project
from bba.preflight import run_preflight
from bba.protocol import ItemDebrief, SolverDebrief
from tests.test_end_state_protocol import manifest


class _PassingBackend:
    def __init__(self, identity):
        self.identity = identity

    def solve(self, identity, bundle, items, repetition, epoch_manifest):
        return [{"id": "preflight-item", "answer": 1}]

    def take_debrief(self):
        return SimpleNamespace(
            items=(SimpleNamespace(item_id="preflight-item"),)
        )

    def take_trace(self):
        return SimpleNamespace(
            usage_metadata_complete=True,
            tool_calls=("submit_predictions", "submit_debrief"),
            identity=self.identity,
            response_model_versions=("fixture-v1",),
            prompt_tokens=2,
            output_tokens=3,
            total_tokens=5,
        )


class _FailingBackend(_PassingBackend):
    def solve(self, identity, bundle, items, repetition, epoch_manifest):
        raise RuntimeError("fixed quota is unavailable")


class TestOperationalReadiness(unittest.TestCase):
    def test_gcp_configuration_populates_native_and_litellm_variables(self):
        value = manifest("gcp-environment-test")
        environment = {}
        configure_gcp_environment(
            value,
            environment,
            lambda **_kwargs: (object(), value.gcp_project),
        )
        self.assertEqual(environment["GOOGLE_CLOUD_PROJECT"], value.gcp_project)
        self.assertEqual(environment["GOOGLE_CLOUD_LOCATION"], "global")
        self.assertEqual(environment["GOOGLE_GENAI_USE_ENTERPRISE"], "TRUE")
        self.assertEqual(environment["VERTEXAI_PROJECT"], value.gcp_project)
        self.assertEqual(environment["VERTEXAI_LOCATION"], "global")

    def test_project_discovery_accepts_litellm_project_environment(self):
        self.assertEqual(
            discover_gcp_project(
                {"VERTEXAI_PROJECT": "bba-test-project"},
                lambda **_kwargs: (object(), None),
            ),
            "bba-test-project",
        )

    def test_project_discovery_error_names_adc_setup(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "gcloud auth application-default login",
        ):
            discover_gcp_project({}, lambda **_kwargs: (object(), None))

    def test_preflight_records_all_model_results_and_can_be_retried(self):
        value = manifest("preflight-report-test")
        with tempfile.TemporaryDirectory(prefix="bba-preflight-report-") as temporary:
            evidence = EvidenceStore(Path(temporary))
            first = {
                identity.artifact_id: _PassingBackend(identity)
                for identity in value.cohort
            }
            first[value.cohort[-1].artifact_id] = _FailingBackend(value.cohort[-1])
            failed = run_preflight(value, evidence, first)

            self.assertFalse(failed["passed"])
            self.assertEqual(len(failed["models"]), len(value.cohort))
            self.assertEqual(failed["models"][-1]["error_type"], "RuntimeError")
            self.assertFalse(
                evidence.record_path(value.epoch_id, "preflight", "vertex").exists()
            )
            self.assertTrue(
                evidence.record_path(
                    value.epoch_id,
                    "preflight-attempts",
                    "vertex",
                ).is_file()
            )

            second = {
                identity.artifact_id: _PassingBackend(identity)
                for identity in value.cohort
            }
            passed = run_preflight(value, evidence, second)
            self.assertTrue(passed["passed"])
            self.assertTrue(
                evidence.record_path(value.epoch_id, "preflight", "vertex").is_file()
            )

    def test_preflight_fails_before_paid_calls_when_sandbox_is_unavailable(self):
        value = manifest("preflight-sandbox-test")
        unavailable = SimpleNamespace(
            available=False,
            unavailable_reason="Bubblewrap namespace probe failed",
        )
        with tempfile.TemporaryDirectory(prefix="bba-preflight-sandbox-") as temporary:
            evidence = EvidenceStore(Path(temporary))
            with patch("bba.preflight.SecureSandbox", return_value=unavailable):
                with patch("bba.preflight.build_adk_solver_backends") as build:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Bubblewrap namespace probe failed",
                    ):
                        run_preflight(value, evidence)
                    build.assert_not_called()

    def test_preflight_rejects_a_different_local_sandbox_backend(self):
        value = manifest("preflight-backend-test")
        available = SimpleNamespace(
            available=True,
            backend="linux-bubblewrap",
            unavailable_reason="",
        )
        with tempfile.TemporaryDirectory(prefix="bba-preflight-backend-") as temporary:
            evidence = EvidenceStore(Path(temporary))
            with patch("bba.preflight.SecureSandbox", return_value=available):
                with patch("bba.preflight.build_adk_solver_backends") as build:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "does not match the frozen epoch manifest",
                    ):
                        run_preflight(value, evidence)
                    build.assert_not_called()

    def test_incomplete_debriefs_are_rejected_instead_of_fabricated(self):
        debrief = SolverDebrief(
            items=(
                ItemDebrief(
                    item_id="item-1",
                    confidence=0.8,
                    approach_tags=("direct",),
                    evidence_refs=(),
                    concise_justification="Solved the first declared item.",
                ),
            )
        )
        with self.assertRaisesRegex(ValueError, "missing=\\['item-2'\\]"):
            _validate_debrief_coverage(debrief, {"item-1", "item-2"})
        self.assertIs(
            _validate_debrief_coverage(debrief, {"item-1"}),
            debrief,
        )

    def test_cli_keeps_cloud_integrations_out_of_top_level_imports(self):
        source = Path(bba.cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("bba.adk_runtime", imported)
        self.assertNotIn("bba.preflight", imported)
        self.assertNotIn("bba.epoch_setup", imported)
        self.assertNotIn("bba.tracing", imported)

    def test_vertex_model_garden_client_is_an_explicit_dependency(self):
        project = (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('"google-cloud-aiplatform>=1.38.0"', project)


if __name__ == "__main__":
    unittest.main()

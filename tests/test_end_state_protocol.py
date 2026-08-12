"""Contract, sandbox, validation, registry, and BBB audit tests."""

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from bba.audit import DefectPair, audit_evaluator
from bba.catalog import CATALOG_VERSION, GCP_LOCATION, SERVERLESS_COHORT
from bba.damage import create_damage_variants
from bba.evidence import EvidenceStore, tree_digest
from bba.protocol import (
    AuditStatus,
    CellState,
    DecisionThresholds,
    ExperimentManifest,
    ModelIdentity,
    ResourceBudget,
    SandboxCapabilities,
    ScoreSummary,
    SolverCell,
    digest_json,
)
from bba.runtime import SandboxUnavailable, SecureSandbox
from tests.fixtures import ExecutableCreatorFixture, LocalFixtureSandbox
from bba.validator import PackageValidator


def cohort():
    return (
        ModelIdentity("google", "creator-a", "family-a", "gemini:creator-a"),
        ModelIdentity(
            "meta", "creator-b", "family-b", "litellm:vertex_ai/meta/creator-b"
        ),
        ModelIdentity(
            "mistral",
            "creator-c",
            "family-c",
            "litellm:vertex_ai/mistral/creator-c",
        ),
        ModelIdentity("google", "creator-d", "family-a", "gemini:creator-d"),
    )


def manifest(epoch_id="protocol-test"):
    hidden = {
        "hidden_solver_panel": ["sealed-solver-a", "sealed-solver-b"],
        "hidden_seeds": [991, 997],
        "audit_policy": {"version": "audit-v1"},
    }
    return ExperimentManifest(
        epoch_id=epoch_id,
        cohort=cohort(),
        catalog_version="fixture-catalog",
        gcp_project="bba-test-project",
        gcp_location="global",
        hidden_commitments={key: digest_json(value) for key, value in hidden.items()},
        creator_prompt_digest="creator-prompt",
        solver_prompt_digest="solver-prompt",
        evaluator_version="public-evaluator-v1",
        sandbox=SandboxCapabilities(backend="trusted-fixture-only"),
    )


class TestEndStateProtocol(unittest.TestCase):
    def test_built_in_catalog_matches_the_public_contract(self):
        self.assertEqual(CATALOG_VERSION, "gcp-serverless-2026-08-12")
        self.assertEqual(GCP_LOCATION, "global")
        self.assertEqual(len(SERVERLESS_COHORT), 12)
        self.assertEqual(len({model.family for model in SERVERLESS_COHORT}), 3)
        self.assertTrue(all("/endpoints/" not in model.model for model in SERVERLESS_COHORT))

    def test_manifest_requires_four_models_and_three_families(self):
        with self.assertRaises(ValueError):
            ExperimentManifest(
                epoch_id="bad",
                cohort=cohort()[:3],
                catalog_version="fixture-catalog",
                gcp_project="bba-test-project",
                gcp_location="global",
                hidden_commitments={key: "a" * 64 for key in ("hidden_solver_panel", "hidden_seeds", "audit_policy")},
                creator_prompt_digest="d",
                solver_prompt_digest="e",
                evaluator_version="v1",
                sandbox=SandboxCapabilities(backend="trusted-fixture-only"),
            )

    def test_model_identity_rejects_deployed_and_direct_endpoints(self):
        with self.assertRaises(ValueError):
            ModelIdentity(
                "meta",
                "projects/bba-test-project/locations/global/endpoints/123",
                "family-a",
                "litellm:vertex_ai/meta/bad",
            )
        with self.assertRaises(ValueError):
            ModelIdentity(
                "meta",
                "https://models.example.test/v1",
                "family-a",
                "litellm:vertex_ai/meta/bad",
            )

    def test_hosted_sandbox_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            SandboxCapabilities(backend="gcp-cloud-run")

    def test_non_success_cell_cannot_carry_score(self):
        with self.assertRaises(ValueError):
            SolverCell(
                snapshot_id="candidate",
                instance_digest="instance",
                solver=cohort()[0],
                repetition=0,
                state=CellState.TIMEOUT,
                invocation_digest="invocation",
                score=ScoreSummary(total=30, correct=0, accuracy=0.0),
            )

    def test_manifest_freeze_is_idempotent_and_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore(Path(temporary))
            path = store.freeze_manifest(manifest())
            original = path.read_bytes()
            self.assertEqual(store.freeze_manifest(manifest()), path)
            with self.assertRaises(ValueError):
                store.freeze_manifest(replace(manifest(), evaluator_version="different"))
            self.assertEqual(path.read_bytes(), original)
            first = store.append_registry_record("history", {"status": "first"})
            second = store.append_registry_record("history", {"status": "second"})
            second_data = json.loads(second.read_text(encoding="utf-8"))
            from bba.evidence import file_digest
            self.assertEqual(second_data["previous_record_digest"], file_digest(first))

    def test_fixture_package_passes_and_noop_generator_fails(self):
        sandbox = LocalFixtureSandbox(acknowledge_unsafe=True)
        validator = PackageValidator(sandbox)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            candidate.mkdir()
            ExecutableCreatorFixture(0.4).build(cohort()[0], 0, candidate, {}, None, manifest())
            valid = validator.validate(
                candidate, "candidate", tree_digest(candidate), 20260811
            )
            self.assertTrue(valid.passed, valid.errors)
            (candidate / "generator.py").write_text(
                '"""BBA_TEST_FIXTURE no-op."""\n', encoding="utf-8"
            )
            invalid = validator.validate(
                candidate, "broken", tree_digest(candidate), 20260811
            )
            self.assertFalse(invalid.passed)
            self.assertTrue(any("missing generated" in error or "gold_private" in error for error in invalid.errors))

    def test_invalid_validation_does_not_claim_an_instance(self):
        sandbox = LocalFixtureSandbox(acknowledge_unsafe=True)
        validator = PackageValidator(sandbox)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            design = root / "design"
            design.mkdir()
            ExecutableCreatorFixture(0.4).build(
                cohort()[0], 0, design, {}, None, manifest()
            )
            (design / "generator.py").write_text(
                '"""BBA_TEST_FIXTURE no-op."""\n', encoding="utf-8"
            )
            record = validator.validate(
                design, "invalid-snapshot", tree_digest(design), 20260811
            )
            self.assertFalse(record.passed)
            self.assertIsNone(record.instance_digest)

    def test_all_controlled_damage_variants_fail_public_validation(self):
        sandbox = LocalFixtureSandbox(acknowledge_unsafe=True)
        validator = PackageValidator(sandbox)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            ExecutableCreatorFixture(0.4).build(cohort()[0], 0, candidate, {}, None, manifest())
            instance = root / "instance"
            record = validator.validate(
                candidate, "base", tree_digest(candidate), 20260811, instance
            )
            self.assertTrue(record.passed, record.errors)
            materialized = root / "materialized"
            shutil.copytree(candidate, materialized)
            shutil.copytree(
                instance / "solver_bundle",
                materialized / "solver_bundle",
                dirs_exist_ok=True,
            )
            shutil.copy2(instance / "gold_private_sample.jsonl", materialized)
            variants = create_damage_variants(materialized, root / "variants")
            self.assertEqual(
                set(variants),
                {"corrupted_key", "duplicate_item", "truncated", "answer_leak", "noop_generator"},
            )
            for name, path in variants.items():
                if name == "noop_generator":
                    (path / "gold_private_sample.jsonl").unlink()
                    (path / "solver_bundle" / "items_private_sample.jsonl").unlink()
                    (path / "solver_bundle" / "SOLVER_MANIFEST.json").unlink()
                    record = validator.validate(
                        path, name, tree_digest(path), 20260811
                    )
                    self.assertFalse(record.passed)
                else:
                    with self.assertRaises(ValueError):
                        validator.validate_materialized_instance(candidate, path)

    def test_secure_sandbox_is_enforced_or_fails_closed(self):
        sandbox = SecureSandbox()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            if not sandbox.available:
                with self.assertRaises(SandboxUnavailable):
                    sandbox.run(["/usr/bin/true"], workspace, 5)
            else:
                result = sandbox.run(
                    [
                        "/usr/bin/python3",
                        "-c",
                        "from pathlib import Path; print(Path('/etc/passwd').read_text())",
                    ],
                    workspace,
                    5,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_holdout_audit_exposes_public_optimizer(self):
        public = {"good": 0.90, "okay": 0.70, "public_optimizer": 0.99, "damaged": 0.20}
        composite = {"good": 0.92, "okay": 0.68, "public_optimizer": 0.30, "damaged": 0.10}
        hidden = {"good": 0.95, "okay": 0.65, "public_optimizer": 0.05, "damaged": 0.15}
        hidden_material = {
            "hidden_solver_panel": ["sealed-solver-a", "sealed-solver-b"],
            "hidden_seeds": [991, 997],
            "audit_policy": {"version": "audit-v1"},
        }
        commitments = manifest().hidden_commitments
        result = audit_evaluator(
            manifest().epoch_id,
            public,
            composite,
            hidden,
            [DefectPair("good", "damaged", "controlled_damage")],
            manifest().thresholds,
            commitments,
            hidden_material,
        )
        self.assertEqual(result["status"], AuditStatus.UNVALIDATED.value)
        self.assertLess(result["targets"]["hidden_only"]["spearman"], 0.5)
        self.assertTrue(result["holdout_retired"])


if __name__ == "__main__":
    unittest.main()

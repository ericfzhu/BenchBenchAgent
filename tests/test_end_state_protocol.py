"""Contract, sandbox, validation, registry, and BBB audit tests."""

import json
import tempfile
import unittest
from pathlib import Path

from bba.audit import DefectPair, audit_evaluator
from bba.damage import create_damage_variants
from bba.evidence import EvidenceStore
from bba.protocol import (
    AuditStatus,
    CellState,
    ExperimentManifest,
    ModelIdentity,
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
        ModelIdentity("google", "creator-a", "family-a"),
        ModelIdentity("meta", "meta/creator-b", "family-b"),
        ModelIdentity("mistral", "mistral/creator-c", "family-c"),
        ModelIdentity("google", "creator-d", "family-a"),
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
        gcp_project="bba-test-project",
        gcp_location="global",
        public_seed=20260811,
        hidden_commitments={key: digest_json(value) for key, value in hidden.items()},
        creator_prompt_digest="creator-prompt",
        solver_prompt_digest="solver-prompt",
        evaluator_version="public-evaluator-v1",
        sandbox=SandboxCapabilities(backend="trusted-fixture-only"),
    )


class TestEndStateProtocol(unittest.TestCase):
    def test_manifest_requires_four_models_and_three_families(self):
        with self.assertRaises(ValueError):
            ExperimentManifest(
                epoch_id="bad",
                cohort=cohort()[:3],
                gcp_project="bba-test-project",
                gcp_location="global",
                public_seed=1,
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
            )
        with self.assertRaises(ValueError):
            ModelIdentity("meta", "https://models.example.test/v1", "family-a")

    def test_non_success_cell_cannot_carry_score(self):
        with self.assertRaises(ValueError):
            SolverCell(
                candidate_digest="candidate",
                solver=cohort()[0],
                repetition=0,
                state=CellState.TIMEOUT,
                invocation_digest="invocation",
                score=ScoreSummary(total=30, correct=0, accuracy=0.0),
            )

    def test_manifest_and_evidence_are_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore(Path(temporary))
            path = store.freeze_manifest(manifest())
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                store.freeze_manifest(manifest())
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
            valid = validator.validate(candidate, "candidate-digest", manifest().public_seed)
            self.assertTrue(valid.passed, valid.errors)
            (candidate / "generator.py").write_text(
                '"""BBA_TEST_FIXTURE no-op."""\n', encoding="utf-8"
            )
            invalid = validator.validate(candidate, "broken-digest", manifest().public_seed)
            self.assertFalse(invalid.passed)
            self.assertTrue(any("missing generated" in error or "gold_private" in error for error in invalid.errors))

    def test_all_controlled_damage_variants_fail_public_validation(self):
        sandbox = LocalFixtureSandbox(acknowledge_unsafe=True)
        validator = PackageValidator(sandbox)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            ExecutableCreatorFixture(0.4).build(cohort()[0], 0, candidate, {}, None, manifest())
            variants = create_damage_variants(candidate, root / "variants")
            self.assertEqual(
                set(variants),
                {"corrupted_key", "duplicate_item", "truncated", "answer_leak", "noop_generator"},
            )
            for name, path in variants.items():
                record = validator.validate(path, name, manifest().public_seed)
                self.assertFalse(record.passed, f"{name} unexpectedly passed")

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

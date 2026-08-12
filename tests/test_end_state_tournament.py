"""End-to-end creator/solver tournament with review and sealed audit."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bba.audit_runner import SealedAuditRunner, build_public_audit_population
from bba.catalog import CATALOG_DIGEST
from bba.evidence import EvidenceStore
from bba.evaluator_identity import build_evaluator_identity
from bba.errors import ProviderFailure
from bba.holdouts import HoldoutRegistry
from bba.protocol import (
    DecisionThresholds,
    ExperimentManifest,
    ModelIdentity,
    PromotionDecision,
    ReviewFindings,
    SandboxCapabilities,
    canonical_json,
    digest_json,
    to_primitive,
)
from tests.fixtures import CalibratedSolverFixture, ExecutableCreatorFixture, LocalFixtureSandbox
from bba.tournament import TournamentController
from bba.replay import replay_solver_attempt
from bba.validator import PackageValidator, read_jsonl_strict


class TrackingPackageValidator(PackageValidator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.solver_score_calls = 0

    def run_package_python(self, package, workspace, script, args, cwd, timeout_seconds=None):
        if Path(script).name == "scorer.py" and "predictions.jsonl" in args:
            self.solver_score_calls += 1
        return super().run_package_python(
            package, workspace, script, args, cwd, timeout_seconds
        )


class TestEndStateTournament(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bba-end-state-")
        self.root = Path(self.temporary.name)
        self.cohort = (
            ModelIdentity("google", "alpha", "family-a", "gemini:alpha"),
            ModelIdentity("meta", "beta", "family-b", "litellm:vertex_ai/meta/beta"),
            ModelIdentity(
                "mistral", "gamma", "family-c", "litellm:vertex_ai/mistral/gamma"
            ),
            ModelIdentity("google", "delta", "family-a", "gemini:delta"),
        )
        self.hidden_material = {
            "hidden_solver_panel": {
                "models": to_primitive(tuple(
                    replace(identity, scaffold="sealed-fixture-v1")
                    for identity in self.cohort
                )),
                "scaffold_seed": 991,
            },
            "hidden_seeds": {
                "generator_seeds": [881, 883, 887],
                "solver_seeds": [907, 911, 919],
            },
            "audit_policy": {"version": "audit-v1"},
        }
        evaluator = build_evaluator_identity(
            "creator-prompt", "solver-prompt", CATALOG_DIGEST
        )
        self.manifest = ExperimentManifest(
            epoch_id="fixture-epoch",
            cohort=self.cohort,
            catalog_version="fixture-catalog",
            gcp_project="bba-test-project",
            gcp_location="global",
            hidden_commitments={key: digest_json(value) for key, value in self.hidden_material.items()},
            creator_prompt_digest="creator-prompt",
            solver_prompt_digest="solver-prompt",
            evaluator_version=evaluator["root_digest"],
            evaluator_components=evaluator,
            sandbox=SandboxCapabilities(backend="trusted-fixture-only"),
        )
        creator_biases = (0.20, 0.28, 0.36, 0.50)
        solver_skills = (0.45, 0.50, 0.55, 0.60)
        evidence = EvidenceStore(self.root)
        evidence.freeze_epoch_setup(self.manifest, self.hidden_material)
        HoldoutRegistry(evidence).transition(
            self.manifest.epoch_id,
            self.manifest.hidden_commitments,
            "committed",
        )
        self.controller = TournamentController(
            self.manifest,
            evidence,
            TrackingPackageValidator(LocalFixtureSandbox(acknowledge_unsafe=True)),
            {
                identity.artifact_id: ExecutableCreatorFixture(bias)
                for identity, bias in zip(self.cohort, creator_biases)
            },
            {
                identity.artifact_id: CalibratedSolverFixture(skill)
                for identity, skill in zip(self.cohort, solver_skills)
            },
        )
        self.review_private_key = Ed25519PrivateKey.generate()
        self.review_private_bytes = self.review_private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.review_public_bytes = self.review_private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.passing_findings = ReviewFindings(
            named_capability_valid=True,
            public_materials_sufficient=True,
            oracle_consistent=True,
            scorer_consistent=True,
            no_arbitrary_obscurity=True,
            useful_evaluation=True,
        )

    def test_automatic_sealed_audit_uses_no_score_files(self):
        class InvalidFinalRound:
            def __init__(self, delegate):
                self.delegate = delegate

            def build(self, identity, round_index, output_dir, *args, **kwargs):
                self.delegate.build(
                    identity, round_index, output_dir, *args, **kwargs
                )
                if round_index == 2:
                    (output_dir / "generator.py").unlink()

        excluded_creator = self.cohort[0].artifact_id
        self.controller.creator_backends[excluded_creator] = InvalidFinalRound(
            self.controller.creator_backends[excluded_creator]
        )
        self.controller.run_public_epoch()
        population = build_public_audit_population(
            self.controller, self.controller.validator
        )
        self.assertIn("control:public-optimizer", population["public_scores"])
        excluded_snapshot = next(
            snapshot
            for snapshot in self.controller.snapshots
            if snapshot.creator.artifact_id == excluded_creator
            and snapshot.round_index == 2
        )
        self.assertNotIn(
            f"base:{excluded_snapshot.snapshot_id}", population["public_scores"]
        )
        self.controller.close_public_epoch()
        hidden_cohort = tuple(
            replace(identity, scaffold="sealed-fixture-v1")
            for identity in self.cohort
        )
        hidden_backends = {
            identity.artifact_id: CalibratedSolverFixture(0.52)
            for identity in hidden_cohort
        }
        audit = SealedAuditRunner(
            self.controller,
            self.controller.validator,
            hidden_backends,
        ).run()
        self.assertIn("composite", audit["targets"])
        self.assertIn("hidden_only", audit["targets"])
        self.assertTrue(audit["holdout_retired"])
        self.assertEqual(len(audit["hidden_solver_cells"]), 36)
        self.assertFalse(any(
            cell["snapshot_id"] == excluded_snapshot.snapshot_id
            for cell in audit["hidden_solver_cells"]
        ))
        hidden_replay = replay_solver_attempt(
            self.controller.evidence,
            self.manifest.epoch_id,
            audit["hidden_solver_cells"][0]["selected_attempt_id"],
        )
        self.assertTrue(hidden_replay["verified"])
        self.assertTrue(
            all(all(checks.values()) for checks in audit["damage_checks"].values())
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_epoch_review_rankings_and_audit(self):
        self.controller.run_public_epoch()
        self.assertEqual(len(self.controller.snapshots), 12)
        self.assertEqual(len(self.controller.instances), 12)
        self.assertEqual(len(self.controller.round_seeds), 3)
        self.assertEqual(sum(len(cells) for cells in self.controller.cells.values()), 144)
        self.assertEqual(self.controller.validator.solver_score_calls, 144)
        self.assertTrue(all(record.passed for record in self.controller.validations.values()))
        first_cell = next(iter(self.controller.cells.values()))[0]
        replay = replay_solver_attempt(
            self.controller.evidence,
            self.manifest.epoch_id,
            first_cell.selected_attempt_id,
        )
        self.assertTrue(replay["verified"])
        self.assertFalse(replay["model_call_used"])
        self.assertEqual(replay["score"], first_cell.score)
        self.assertEqual(replay["debrief_digest"], first_cell.debrief_digest)
        selected_attempt = next(
            attempt
            for attempts in self.controller.attempts.values()
            for attempt in attempts
            if attempt.attempt_id == first_cell.selected_attempt_id
        )
        self.assertIn("debrief", selected_attempt.evidence_files)

        for round_index in range(3):
            round_snapshots = [
                snapshot for snapshot in self.controller.snapshots
                if snapshot.round_index == round_index
            ]
            self.assertEqual(len(round_snapshots), 4)
            self.assertEqual(
                {self.controller.instances[snapshot.snapshot_id].seed for snapshot in round_snapshots},
                {self.controller.round_seeds[round_index]},
            )
            for snapshot in round_snapshots:
                design = Path(snapshot.design_path)
                self.assertFalse((design / "gold_private_sample.jsonl").exists())
                self.assertFalse(
                    (design / "solver_bundle" / "items_private_sample.jsonl").exists()
                )
                spec = json.loads(
                    (design / "benchmark_spec.json").read_text(encoding="utf-8")
                )
                if round_index == 0:
                    self.assertEqual(spec["feedback_debrief_count"], 0)
                else:
                    self.assertGreater(spec["feedback_debrief_count"], 0)

        round_zero = next(
            snapshot
            for snapshot in self.controller.snapshots
            if snapshot.creator == self.cohort[0] and snapshot.round_index == 0
        )
        public_feedback = self.controller._feedback(round_zero)
        self.assertLessEqual(len(public_feedback["solver_debriefs"]), 150)
        self.assertLessEqual(
            len(canonical_json(public_feedback["solver_debriefs"])),
            48 * 1024,
        )

        for identity in self.cohort:
            chain = [
                snapshot for snapshot in self.controller.snapshots
                if snapshot.creator.artifact_id == identity.artifact_id
            ]
            self.assertIsNone(chain[0].parent_snapshot_id)
            self.assertEqual(chain[1].parent_snapshot_id, chain[0].snapshot_id)
            self.assertEqual(chain[2].parent_snapshot_id, chain[1].snapshot_id)

        final_snapshots = [snapshot for snapshot in self.controller.snapshots if snapshot.round_index == 2]
        for snapshot in final_snapshots:
            gold = {
                row["id"]: row["answer"]
                for row in read_jsonl_strict(
                    Path(self.controller.instances[snapshot.snapshot_id].instance_path)
                    / "gold_private_sample.jsonl"
                )
            }
            selected = self.controller.select_review_items(snapshot)
            signed = self.controller.record_human_review(
                snapshot,
                reviewer_id="independent-reviewer",
                reconstructed_answers={item_id: gold[item_id] for item_id in selected},
                decision=PromotionDecision.APPROVED,
                findings=self.passing_findings,
                limitations=("Synthetic conformance fixture",),
                key_id="reviewer-key-1",
                signing_key=self.review_private_bytes,
                public_key=self.review_public_bytes,
            )
            self.assertTrue(signed.signature)
            repeated = self.controller.record_human_review(
                snapshot,
                reviewer_id="independent-reviewer",
                reconstructed_answers={item_id: gold[item_id] for item_id in selected},
                decision=PromotionDecision.APPROVED,
                findings=self.passing_findings,
                limitations=("Synthetic conformance fixture",),
                key_id="reviewer-key-1",
                signing_key=self.review_private_bytes,
                public_key=self.review_public_bytes,
            )
            self.assertEqual(repeated, signed)

        population = build_public_audit_population(
            self.controller, self.controller.validator
        )
        self.assertIn("control:public-optimizer", population["public_scores"])
        public = self.controller.close_public_epoch()
        self.assertEqual(self.controller.close_public_epoch(), public)
        statuses = public["candidate_statuses"]
        final_statuses = [statuses[snapshot.snapshot_id] for snapshot in final_snapshots]
        self.assertEqual(final_statuses.count("active"), 3)
        self.assertEqual(final_statuses.count("frontier_challenge"), 1)
        self.assertEqual(len(public["creator_rankings"]["blind_round"]), 4)
        self.assertEqual(len(public["creator_rankings"]["final_round"]), 4)
        self.assertTrue(all(row["canonical_benchmarks"] == 3 for row in public["solver_ranking"]))
        self.assertFalse(public["hidden_evidence_included"])

        cell_path = self.controller.evidence.record_path(
            self.manifest.epoch_id,
            "solver-cells",
            self.controller._cell_record_id(
                self.controller.snapshot_by_id(first_cell.snapshot_id),
                first_cell.solver,
                first_cell.repetition,
            ),
        )
        original_cell = cell_path.read_bytes()
        tampered_cell = json.loads(original_cell)
        tampered_cell["debrief_digest"] = "0" * 64
        cell_path.write_bytes(canonical_json(tampered_cell) + b"\n")
        try:
            with self.assertRaisesRegex(ValueError, "solver cell selection"):
                TournamentController(self.manifest, self.controller.evidence)
        finally:
            cell_path.write_bytes(original_cell)


    def test_public_epoch_resumes_after_an_interrupted_creator(self):
        class InterruptOnce:
            def __init__(self, delegate):
                self.delegate = delegate
                self.interrupted = False

            def build(self, *args, **kwargs):
                if not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt("simulated local process stop")
                return self.delegate.build(*args, **kwargs)

        manifest = replace(
            self.manifest,
            epoch_id="resume-fixture-epoch",
            thresholds=DecisionThresholds(sample_count=6, solver_repetitions=1),
        )
        creators = {
            identity.artifact_id: ExecutableCreatorFixture(0.25)
            for identity in self.cohort
        }
        first_identity = self.cohort[0].artifact_id
        creators[first_identity] = InterruptOnce(creators[first_identity])
        solvers = {
            identity.artifact_id: CalibratedSolverFixture(0.55)
            for identity in self.cohort
        }
        evidence = EvidenceStore(self.root)
        validator = PackageValidator(
            LocalFixtureSandbox(acknowledge_unsafe=True), sample_count=6
        )
        interrupted = TournamentController(
            manifest, evidence, validator, creators, solvers
        )
        with self.assertRaises(KeyboardInterrupt):
            interrupted.run_public_epoch()
        self.assertEqual(
            interrupted.epoch_status()["work_counts"].get("running"), 1
        )

        resumed = TournamentController(
            manifest, evidence, validator, creators, solvers
        )
        self.assertEqual(resumed.state.recover_interrupted(manifest.epoch_id), 1)
        resumed.run_public_epoch()
        self.assertEqual(len(resumed.snapshots), 12)
        self.assertEqual(sum(len(items) for items in resumed.cells.values()), 48)
        self.assertEqual(resumed.epoch_status()["phase"], "awaiting_review")

        restored = TournamentController(manifest, evidence)
        self.assertEqual(len(restored.snapshots), 12)
        self.assertEqual(len(restored.instances), 12)
        self.assertEqual(len(restored.round_seeds), 3)
        self.assertEqual(restored.epoch_status()["work_counts"], {"succeeded": 72})

    def test_round_seed_is_selected_only_after_all_round_designs_freeze(self):
        class ObserveSeedBarrier:
            def __init__(self, delegate, evidence, epoch_id):
                self.delegate = delegate
                self.evidence = evidence
                self.epoch_id = epoch_id

            def build(self, identity, round_index, *args, **kwargs):
                seed_path = (
                    self.evidence.epoch_root(self.epoch_id)
                    / "round-seeds"
                    / f"round-{round_index}.json"
                )
                if seed_path.exists():
                    raise AssertionError("round seed existed during creator construction")
                return self.delegate.build(identity, round_index, *args, **kwargs)

        manifest = replace(
            self.manifest,
            epoch_id="seed-barrier-fixture",
            thresholds=DecisionThresholds(sample_count=6, solver_repetitions=1),
        )
        evidence = EvidenceStore(self.root)
        creators = {
            identity.artifact_id: ObserveSeedBarrier(
                ExecutableCreatorFixture(0.25), evidence, manifest.epoch_id
            )
            for identity in self.cohort
        }
        solvers = {
            identity.artifact_id: CalibratedSolverFixture(0.55)
            for identity in self.cohort
        }
        controller = TournamentController(
            manifest,
            evidence,
            PackageValidator(
                LocalFixtureSandbox(acknowledge_unsafe=True), sample_count=6
            ),
            creators,
            solvers,
        )
        controller.run_public_epoch()
        self.assertEqual(len(controller.round_seeds), 3)

    def test_public_epoch_resumes_after_seed_freeze(self):
        class InterruptValidationOnce(PackageValidator):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.interrupted = False

            def validate(self, *args, **kwargs):
                if not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt("simulated stop after seed freeze")
                return super().validate(*args, **kwargs)

        manifest = replace(
            self.manifest,
            epoch_id="seed-resume-fixture",
            thresholds=DecisionThresholds(sample_count=6, solver_repetitions=1),
        )
        evidence = EvidenceStore(self.root)
        creators = {
            identity.artifact_id: ExecutableCreatorFixture(0.25)
            for identity in self.cohort
        }
        solvers = {
            identity.artifact_id: CalibratedSolverFixture(0.55)
            for identity in self.cohort
        }
        interrupted = TournamentController(
            manifest,
            evidence,
            InterruptValidationOnce(
                LocalFixtureSandbox(acknowledge_unsafe=True), sample_count=6
            ),
            creators,
            solvers,
        )
        with self.assertRaises(KeyboardInterrupt):
            interrupted.run_public_epoch()
        frozen_seed = interrupted.round_seeds[0]

        resumed = TournamentController(
            manifest,
            evidence,
            PackageValidator(
                LocalFixtureSandbox(acknowledge_unsafe=True), sample_count=6
            ),
            creators,
            solvers,
        )
        self.assertEqual(resumed.round_seeds[0], frozen_seed)
        self.assertEqual(resumed.state.recover_interrupted(manifest.epoch_id), 1)
        resumed.run_public_epoch()
        self.assertEqual(resumed.round_seeds[0], frozen_seed)
        self.assertEqual(resumed.epoch_status()["phase"], "awaiting_review")

    def test_provider_failures_retry_and_keep_every_attempt(self):
        class FailTwice:
            def __init__(self, delegate):
                self.delegate = delegate
                self.calls = 0

            def solve(self, *args, **kwargs):
                self.calls += 1
                if self.calls <= 2:
                    raise ProviderFailure(f"fixture provider failure {self.calls}")
                return self.delegate.solve(*args, **kwargs)

            def take_debrief(self):
                return self.delegate.take_debrief()

        manifest = replace(
            self.manifest,
            epoch_id="retry-fixture",
            thresholds=DecisionThresholds(sample_count=6, solver_repetitions=1),
        )
        evidence = EvidenceStore(self.root)
        creators = {
            identity.artifact_id: ExecutableCreatorFixture(0.25)
            for identity in self.cohort
        }
        solvers = {
            identity.artifact_id: CalibratedSolverFixture(0.55)
            for identity in self.cohort
        }
        target = self.cohort[0].artifact_id
        solvers[target] = FailTwice(solvers[target])
        controller = TournamentController(
            manifest,
            evidence,
            PackageValidator(
                LocalFixtureSandbox(acknowledge_unsafe=True), sample_count=6
            ),
            creators,
            solvers,
        )
        controller.run_public_epoch()
        target_cells = [
            cell
            for cells in controller.cells.values()
            for cell in cells
            if cell.solver.artifact_id == target
        ]
        retried = [cell for cell in target_cells if len(cell.attempt_ids) == 3]
        self.assertEqual(len(retried), 1)
        cell = retried[0]
        attempts = controller.attempts[
            controller._cell_record_id(
                controller.snapshot_by_id(cell.snapshot_id),
                cell.solver,
                cell.repetition,
            )
        ]
        self.assertEqual(
            [item.state.value for item in attempts],
            ["provider_error", "provider_error", "success"],
        )
        self.assertEqual(cell.selected_attempt_id, attempts[-1].attempt_id)

    def test_approval_rejects_a_failed_construct_finding(self):
        self.controller.run_public_epoch()
        snapshot = next(
            item for item in self.controller.snapshots if item.round_index == 2
        )
        gold = {
            row["id"]: row["answer"]
            for row in read_jsonl_strict(
                Path(self.controller.instances[snapshot.snapshot_id].instance_path)
                / "gold_private_sample.jsonl"
            )
        }
        selected = self.controller.select_review_items(snapshot)
        failed = replace(self.passing_findings, useful_evaluation=False)
        with self.assertRaisesRegex(ValueError, "construct-validity"):
            self.controller.record_human_review(
                snapshot,
                "independent-reviewer",
                {item_id: gold[item_id] for item_id in selected},
                PromotionDecision.APPROVED,
                failed,
                (),
                "reviewer-key-failed",
                self.review_private_bytes,
                self.review_public_bytes,
            )


if __name__ == "__main__":
    unittest.main()

"""End-to-end creator/solver tournament with review and sealed audit."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bba.audit import DefectPair
from bba.evidence import EvidenceStore
from bba.errors import ProviderFailure
from bba.protocol import (
    AuditStatus,
    DecisionThresholds,
    ExperimentManifest,
    ModelIdentity,
    PromotionDecision,
    ReviewFindings,
    SandboxCapabilities,
    digest_json,
)
from tests.fixtures import CalibratedSolverFixture, ExecutableCreatorFixture, LocalFixtureSandbox
from bba.tournament import TournamentController
from bba.replay import replay_solver_attempt
from bba.validator import PackageValidator, read_jsonl_strict


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
            "hidden_solver_panel": ["sealed-a", "sealed-b"],
            "hidden_seeds": [881, 883],
            "audit_policy": {"version": "audit-v1"},
        }
        self.manifest = ExperimentManifest(
            epoch_id="fixture-epoch",
            cohort=self.cohort,
            catalog_version="fixture-catalog",
            gcp_project="bba-test-project",
            gcp_location="global",
            hidden_commitments={key: digest_json(value) for key, value in self.hidden_material.items()},
            creator_prompt_digest="creator-prompt",
            solver_prompt_digest="solver-prompt",
            evaluator_version="a" * 64,
            sandbox=SandboxCapabilities(backend="trusted-fixture-only"),
        )
        creator_biases = (0.20, 0.28, 0.36, 0.50)
        solver_skills = (0.45, 0.50, 0.55, 0.60)
        self.controller = TournamentController(
            self.manifest,
            EvidenceStore(self.root),
            PackageValidator(LocalFixtureSandbox(acknowledge_unsafe=True)),
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

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_epoch_review_rankings_and_audit(self):
        self.controller.run_public_epoch()
        self.assertEqual(len(self.controller.snapshots), 12)
        self.assertEqual(len(self.controller.instances), 12)
        self.assertEqual(len(self.controller.round_seeds), 3)
        self.assertEqual(sum(len(cells) for cells in self.controller.cells.values()), 144)
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

        for identity in self.cohort:
            chain = [
                snapshot for snapshot in self.controller.snapshots
                if snapshot.creator.artifact_id == identity.artifact_id
            ]
            self.assertIsNone(chain[0].parent_snapshot_id)
            self.assertEqual(chain[1].parent_snapshot_id, chain[0].snapshot_id)
            self.assertEqual(chain[2].parent_snapshot_id, chain[1].snapshot_id)

        with self.assertRaises(RuntimeError):
            self.controller.run_holdout_audit({}, {}, self.hidden_material)

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

        public_scores = {"good": 0.90, "okay": 0.70, "optimizer": 0.99, "damaged": 0.20}
        defect_pairs = [DefectPair("good", "damaged", "controlled_damage")]
        population = self.controller.freeze_audit_population(public_scores, defect_pairs)
        self.assertEqual(
            self.controller.freeze_audit_population(public_scores, defect_pairs),
            population,
        )
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

        # A controlled public optimizer tops the visible score while collapsing
        # on the sealed target; the audit must not validate the evaluator.
        composite = {"good": 0.92, "okay": 0.68, "optimizer": 0.30, "damaged": 0.10}
        hidden = {"good": 0.95, "okay": 0.65, "optimizer": 0.05, "damaged": 0.15}
        audit = self.controller.run_holdout_audit(
            composite,
            hidden,
            self.hidden_material,
        )
        self.assertEqual(audit["status"], AuditStatus.UNVALIDATED.value)
        self.assertIn("pairwise_within_public_top_quartile", audit["targets"]["hidden_only"])
        self.assertTrue(audit["holdout_retired"])
        self.assertEqual(
            self.controller.run_holdout_audit(composite, hidden, self.hidden_material),
            audit,
        )

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

"""End-to-end creator/solver tournament with review and sealed audit."""

import tempfile
import unittest
from pathlib import Path

from bba.audit import DefectPair
from bba.evidence import EvidenceStore
from bba.protocol import AuditStatus, ExperimentManifest, ModelIdentity, PromotionDecision, digest_json
from tests.fixtures import CalibratedSolverFixture, ExecutableCreatorFixture, LocalFixtureSandbox
from bba.tournament import TournamentController
from bba.validator import PackageValidator, read_jsonl_strict


class TestEndStateTournament(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bba-end-state-")
        self.root = Path(self.temporary.name)
        self.cohort = (
            ModelIdentity("openai", "alpha", "family-a"),
            ModelIdentity("anthropic", "beta", "family-b"),
            ModelIdentity("google", "gamma", "family-c"),
            ModelIdentity("openai", "delta", "family-a"),
        )
        self.hidden_material = {
            "hidden_solver_panel": ["sealed-a", "sealed-b"],
            "hidden_seeds": [881, 883],
            "audit_policy": {"version": "audit-v1"},
        }
        self.manifest = ExperimentManifest(
            epoch_id="fixture-epoch",
            cohort=self.cohort,
            public_seed=20260811,
            hidden_commitments={key: digest_json(value) for key, value in self.hidden_material.items()},
            creator_prompt_digest="creator-prompt",
            solver_prompt_digest="solver-prompt",
            evaluator_version="public-evaluator-v1",
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

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_epoch_review_rankings_and_audit(self):
        self.controller.run_public_epoch()
        self.assertEqual(len(self.controller.snapshots), 12)
        self.assertEqual(sum(len(cells) for cells in self.controller.cells.values()), 144)
        self.assertTrue(all(record.passed for record in self.controller.validations.values()))

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
                for row in read_jsonl_strict(Path(snapshot.package_path) / "gold_private_sample.jsonl")
            }
            selected = self.controller.select_review_items(snapshot)
            signed = self.controller.record_human_review(
                snapshot,
                reviewer_id="independent-reviewer",
                reconstructed_answers={item_id: gold[item_id] for item_id in selected},
                decision=PromotionDecision.APPROVED,
                limitations=("Synthetic conformance fixture",),
                key_id="reviewer-key-1",
                signing_key=b"test-only-reviewer-secret",
            )
            self.assertTrue(signed.signature)

        public_scores = {"good": 0.90, "okay": 0.70, "optimizer": 0.99, "damaged": 0.20}
        defect_pairs = [DefectPair("good", "damaged", "controlled_damage")]
        self.controller.freeze_audit_population(public_scores, defect_pairs)
        public = self.controller.close_public_epoch()
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


if __name__ == "__main__":
    unittest.main()

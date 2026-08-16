"""Restart recovery tests for sealed-audit hidden instances."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bba.audit_runner import SealedAuditRunner
from bba.evidence import EvidenceStore, atomic_publish_json, tree_digest
from bba.protocol import EvaluationInstance


class RecordingValidator:
    def __init__(self) -> None:
        self.calls = 0
        self.sandbox = SimpleNamespace(
            available=True,
            backend="linux-bubblewrap",
            unavailable_reason="",
        )

    def validate(
        self,
        _design,
        snapshot_id,
        _design_digest,
        seed,
        output,
    ):
        self.calls += 1
        output.mkdir(parents=True)
        (output / "payload.txt").write_text(
            f"{snapshot_id}:{seed}\n",
            encoding="utf-8",
        )
        return SimpleNamespace(passed=True, errors=())


class TestHiddenInstanceRecovery(unittest.TestCase):
    def _runner(self, root: Path):
        evidence = EvidenceStore(root)
        manifest = SimpleNamespace(
            epoch_id="hidden-instance-recovery",
            sandbox=SimpleNamespace(backend="linux-bubblewrap"),
            thresholds=SimpleNamespace(sample_count=30),
        )
        controller = SimpleNamespace(
            evidence=evidence,
            manifest=manifest,
            validator=None,
        )
        validator = RecordingValidator()
        runner = SealedAuditRunner(controller, validator, {})
        design = root / "design"
        design.mkdir()
        snapshot = SimpleNamespace(
            snapshot_id="snapshot-a",
            design_path=str(design),
            design_digest="d" * 64,
            round_index=2,
        )
        return runner, validator, snapshot

    @staticmethod
    def _paths(runner, snapshot, seed: int):
        profile_id = f"{snapshot.snapshot_id}--seed-{seed}"
        final_root = (
            runner.evidence.epoch_root(runner.controller.manifest.epoch_id)
            / "audit"
            / "hidden-instances"
            / profile_id
        )
        staging = final_root.parent / f".{profile_id}.building"
        return profile_id, final_root, staging

    def test_complete_staging_directory_is_promoted_without_revalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner, validator, snapshot = self._runner(Path(temporary))
            seed = 17
            profile_id, final_root, staging = self._paths(
                runner, snapshot, seed
            )
            staged_data = staging / "data"
            staged_data.mkdir(parents=True)
            (staged_data / "payload.txt").write_text(
                "complete\n",
                encoding="utf-8",
            )
            instance = EvaluationInstance(
                instance_id=profile_id,
                snapshot_id=snapshot.snapshot_id,
                design_digest=snapshot.design_digest,
                instance_digest=tree_digest(staged_data),
                round_index=snapshot.round_index,
                seed=seed,
                sample_count=30,
                created_at="2026-08-16T00:00:00+00:00",
                instance_path=str(final_root / "data"),
            )
            atomic_publish_json(staging / "instance.json", instance)

            recovered = runner._freeze_hidden_instance(snapshot, seed)

            self.assertEqual(validator.calls, 0)
            self.assertFalse(staging.exists())
            self.assertTrue((final_root / "instance.json").is_file())
            self.assertEqual(recovered, instance)
            self.assertEqual(
                tree_digest(final_root / "data"),
                instance.instance_digest,
            )

    def test_partial_staging_directory_is_discarded_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner, validator, snapshot = self._runner(Path(temporary))
            seed = 23
            _profile_id, final_root, staging = self._paths(
                runner, snapshot, seed
            )
            staged_data = staging / "data"
            staged_data.mkdir(parents=True)
            (staged_data / "partial.txt").write_text(
                "interrupted\n",
                encoding="utf-8",
            )

            recovered = runner._freeze_hidden_instance(snapshot, seed)

            self.assertEqual(validator.calls, 1)
            self.assertFalse(staging.exists())
            self.assertTrue((final_root / "instance.json").is_file())
            self.assertTrue((final_root / "data" / "payload.txt").is_file())
            self.assertEqual(recovered.instance_path, str(final_root / "data"))


if __name__ == "__main__":
    unittest.main()

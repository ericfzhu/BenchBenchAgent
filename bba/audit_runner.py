"""Public sealed-audit API with frozen sandbox conformance and recovery."""

from __future__ import annotations

import shutil
from pathlib import Path

from bba._audit_runner import *  # noqa: F401,F403
from bba._audit_runner import (
    SealedAuditRunner as _SealedAuditRunner,
    build_public_audit_population as _build_public_audit_population,
)
from bba.evidence import read_json, tree_digest
from bba.protocol import EvaluationInstance


def _validate_audit_sandbox(controller, validator) -> None:
    sandbox = validator.sandbox
    if not sandbox.available:
        raise RuntimeError(
            sandbox.unavailable_reason or "secure local sandbox is unavailable"
        )
    if sandbox.backend != controller.manifest.sandbox.backend:
        raise ValueError("audit sandbox does not match the frozen epoch manifest")


def build_public_audit_population(controller, validator):
    _validate_audit_sandbox(controller, validator)
    return _build_public_audit_population(controller, validator)


class SealedAuditRunner(_SealedAuditRunner):
    """Sealed audit runner with restart-safe hidden-instance publication."""

    def __init__(self, controller, validator, hidden_solver_backends):
        _validate_audit_sandbox(controller, validator)
        super().__init__(controller, validator, hidden_solver_backends)

    @staticmethod
    def _discard_hidden_instance_staging(path: Path) -> None:
        """Remove only an unpublished hidden-instance staging path."""

        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _load_staged_hidden_instance(
        self,
        staging: Path,
        final_root: Path,
        snapshot,
        seed: int,
        profile_id: str,
    ) -> EvaluationInstance | None:
        """Validate a complete staged instance before publishing it."""

        if staging.is_symlink() or not staging.is_dir():
            return None
        metadata = staging / "instance.json"
        staged_data = staging / "data"
        if (
            not metadata.is_file()
            or metadata.is_symlink()
            or not staged_data.is_dir()
            or staged_data.is_symlink()
        ):
            return None

        value = read_json(metadata)
        instance = EvaluationInstance(**value)
        expected_identity = (
            profile_id,
            snapshot.snapshot_id,
            snapshot.design_digest,
            snapshot.round_index,
            int(seed),
            self.controller.manifest.thresholds.sample_count,
            str(final_root / "data"),
        )
        actual_identity = (
            instance.instance_id,
            instance.snapshot_id,
            instance.design_digest,
            instance.round_index,
            instance.seed,
            instance.sample_count,
            instance.instance_path,
        )
        if actual_identity != expected_identity:
            raise ValueError(
                f"staged hidden instance has the wrong identity: {profile_id}"
            )
        if tree_digest(staged_data) != instance.instance_digest:
            raise ValueError(
                f"staged hidden instance digest is invalid: {profile_id}"
            )
        return instance

    def _freeze_hidden_instance(self, snapshot, seed: int) -> EvaluationInstance:
        """Recover or rebuild an interrupted hidden-instance publication."""

        profile_id = f"{snapshot.snapshot_id}--seed-{seed}"
        final_root = (
            self.evidence.epoch_root(self.controller.manifest.epoch_id)
            / "audit"
            / "hidden-instances"
            / profile_id
        )
        if final_root.exists():
            return super()._freeze_hidden_instance(snapshot, seed)

        staging = final_root.parent / f".{profile_id}.building"
        if staging.exists() or staging.is_symlink():
            try:
                instance = self._load_staged_hidden_instance(
                    staging,
                    final_root,
                    snapshot,
                    seed,
                    profile_id,
                )
            except (KeyError, OSError, TypeError, ValueError):
                instance = None

            if instance is None:
                self._discard_hidden_instance_staging(staging)
            else:
                final_root.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(final_root)
                return instance

        return super()._freeze_hidden_instance(snapshot, seed)

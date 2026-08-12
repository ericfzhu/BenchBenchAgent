"""Execute the sealed BBA holdout from committed local material."""

from __future__ import annotations

import json
import shutil
import statistics
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from bba.audit import audit_evaluator
from bba.damage import create_damage_variants
from bba.evidence import EvidenceStore, atomic_publish_json, read_json, tree_digest
from bba.holdouts import HoldoutRegistry
from bba.protocol import (
    CellState,
    EvaluationInstance,
    ModelIdentity,
    SolverAttempt,
    canonical_json,
    digest_json,
    model_identity_from_mapping,
    to_primitive,
)
from bba.tournament import SolverBackend, TournamentController
from bba.validator import PackageValidator


def build_public_audit_population(
    controller: TournamentController,
    validator: PackageValidator,
) -> Dict[str, Any]:
    if controller._public_closed:
        raise RuntimeError("public audit population must freeze before public closure")
    if not controller._public_run_is_complete():
        raise RuntimeError("public audit population requires a complete public run")
    runner = SealedAuditRunner(controller, validator, {})
    scores: Dict[str, float] = {}
    pairs = []
    final_round = controller.manifest.thresholds.rounds - 1
    evaluations = {
        item.snapshot.snapshot_id: item for item in controller.candidate_evaluations()
    }
    for snapshot in controller.snapshots:
        if snapshot.round_index != final_round:
            continue
        evaluation = evaluations[snapshot.snapshot_id]
        if evaluation.public_quality is None:
            continue
        base_id = f"base:{snapshot.snapshot_id}"
        scores[base_id] = evaluation.public_quality
        checks = runner._damage_checks(
            snapshot, controller.instances[snapshot.snapshot_id]
        )
        if not all(checks.values()):
            raise RuntimeError(f"public evaluator missed controlled damage: {base_id}")
        for category in sorted(checks):
            damaged_id = f"damaged:{snapshot.snapshot_id}:{category}"
            scores[damaged_id] = 0.0
            from bba.audit import DefectPair

            pairs.append(DefectPair(base_id, damaged_id, category))
    scores["control:public-optimizer"] = 1.0
    if len(scores) < 2:
        raise RuntimeError("public audit population has too few profiles")
    return controller.freeze_audit_population(scores, pairs)


class SealedAuditRunner:
    def __init__(
        self,
        controller: TournamentController,
        validator: PackageValidator,
        hidden_solver_backends: Mapping[str, SolverBackend],
    ):
        self.controller = controller
        self.evidence = controller.evidence
        self.validator = validator
        self.controller.validator = validator
        self.hidden_solver_backends = dict(hidden_solver_backends)

    def _open_private_material(self) -> Dict[str, Any]:
        if not self.controller._public_closed:
            raise RuntimeError("sealed material cannot open before public closure")
        path = (
            self.evidence.epoch_root(self.controller.manifest.epoch_id)
            / "private"
            / "holdout-plan.json"
        )
        material = read_json(path)
        actual = {key: digest_json(value) for key, value in material.items()}
        if actual != dict(self.controller.manifest.hidden_commitments):
            raise ValueError("sealed material does not match its manifest commitments")
        HoldoutRegistry(self.evidence).transition(
            self.controller.manifest.epoch_id,
            self.controller.manifest.hidden_commitments,
            "opened",
        )
        return material

    def _hidden_identities(self, material: Mapping[str, Any]) -> tuple[ModelIdentity, ...]:
        identities = tuple(
            model_identity_from_mapping(item)
            for item in material["hidden_solver_panel"]["models"]
        )
        expected = {item.artifact_id for item in identities}
        if set(self.hidden_solver_backends) != expected:
            raise ValueError("hidden solver backends do not match the committed hidden panel")
        public = {item.artifact_id for item in self.controller.manifest.cohort}
        if public.intersection(expected):
            raise ValueError("hidden solver configurations must be distinct from the public panel")
        return identities

    def _freeze_hidden_instance(self, snapshot, seed: int) -> EvaluationInstance:
        profile_id = f"{snapshot.snapshot_id}--seed-{seed}"
        root = (
            self.evidence.epoch_root(self.controller.manifest.epoch_id)
            / "audit"
            / "hidden-instances"
            / profile_id
        )
        metadata = root / "instance.json"
        data = root / "data"
        if metadata.is_file() and data.is_dir():
            value = read_json(metadata)
            if tree_digest(data) != value["instance_digest"]:
                raise ValueError(f"hidden instance digest is invalid: {profile_id}")
            value["instance_path"] = str(data)
            return EvaluationInstance(**value)
        if root.exists():
            raise RuntimeError(f"hidden instance is incomplete: {profile_id}")
        with tempfile.TemporaryDirectory(prefix="bba-hidden-instance-") as temporary:
            output = Path(temporary) / "data"
            validation = self.validator.validate(
                Path(snapshot.design_path),
                snapshot.snapshot_id,
                snapshot.design_digest,
                seed,
                output,
            )
            if not validation.passed:
                raise ValueError(
                    f"benchmark did not transfer to hidden seed {seed}: {validation.errors}"
                )
            digest = tree_digest(output)
            instance = EvaluationInstance(
                instance_id=profile_id,
                snapshot_id=snapshot.snapshot_id,
                design_digest=snapshot.design_digest,
                instance_digest=digest,
                round_index=snapshot.round_index,
                seed=seed,
                sample_count=self.controller.manifest.thresholds.sample_count,
                created_at=validation_record_time(validation),
                instance_path=str(data),
            )
            root.parent.mkdir(parents=True, exist_ok=True)
            temporary_root = root.parent / f".{profile_id}.building"
            if temporary_root.exists():
                raise RuntimeError("hidden instance has an incomplete prior build")
            shutil.copytree(output, temporary_root / "data")
            atomic_publish_json(temporary_root / "instance.json", replace(instance, instance_path=str(data)))
            temporary_root.rename(root)
            return instance

    def _run_hidden_cell(
        self,
        snapshot,
        instance: EvaluationInstance,
        solver: ModelIdentity,
        seed_index: int,
    ):
        original_instance = self.controller.instances.get(snapshot.snapshot_id)
        original_backend = self.controller.solver_backends.get(solver.artifact_id)
        original_payload = self.controller._solver_payload

        def hidden_payload(selected_snapshot, selected_solver, repetition):
            return {
                "epoch_digest": self.controller.manifest.digest,
                "audit": "sealed-hidden-panel-v1",
                "snapshot_id": selected_snapshot.snapshot_id,
                "instance_digest": instance.instance_digest,
                "solver": to_primitive(selected_solver),
                "hidden_seed_index": seed_index,
                "budget": to_primitive(self.controller.manifest.budget),
            }

        self.controller.instances[snapshot.snapshot_id] = instance
        self.controller.solver_backends[solver.artifact_id] = self.hidden_solver_backends[
            solver.artifact_id
        ]
        self.controller._solver_payload = hidden_payload
        try:
            cell_id = self.controller._cell_record_id(snapshot, solver, seed_index)
            attempts = list(self.controller.attempts.get(cell_id, ()))
            while True:
                if attempts and attempts[-1].state == CellState.SUCCESS:
                    break
                if attempts and attempts[-1].state.value not in set(
                    self.controller.manifest.retry_policy.retryable_states
                ):
                    break
                if len(attempts) >= self.controller.manifest.retry_policy.max_attempts:
                    break
                attempt = self.controller._run_solver_attempt(
                    snapshot, solver, seed_index, len(attempts) + 1
                )
                attempts.append(attempt)
                self.controller.attempts[cell_id] = list(attempts)
            if not attempts:
                raise RuntimeError("hidden solver cell produced no immutable attempt")
            return self.controller._select_solver_cell(
                snapshot, solver, seed_index, attempts
            )
        finally:
            self.controller._solver_payload = original_payload
            if original_instance is None:
                self.controller.instances.pop(snapshot.snapshot_id, None)
            else:
                self.controller.instances[snapshot.snapshot_id] = original_instance
            if original_backend is None:
                self.controller.solver_backends.pop(solver.artifact_id, None)
            else:
                self.controller.solver_backends[solver.artifact_id] = original_backend

    def _damage_checks(self, snapshot, instance: EvaluationInstance) -> Dict[str, bool]:
        with tempfile.TemporaryDirectory(prefix="bba-audit-damage-") as temporary:
            root = Path(temporary)
            package = root / "materialized"
            shutil.copytree(Path(snapshot.design_path), package)
            shutil.copytree(
                Path(instance.instance_path) / "solver_bundle",
                package / "solver_bundle",
                dirs_exist_ok=True,
            )
            shutil.copy2(
                Path(instance.instance_path) / "gold_private_sample.jsonl", package
            )
            variants = create_damage_variants(package, root / "variants")
            checks = {}
            for name, variant in variants.items():
                try:
                    if name == "noop_generator":
                        for output in (
                            variant / "gold_private_sample.jsonl",
                            variant / "solver_bundle" / "items_private_sample.jsonl",
                            variant / "solver_bundle" / "SOLVER_MANIFEST.json",
                        ):
                            output.unlink(missing_ok=True)
                        record = self.validator.validate(
                            variant,
                            f"audit-{name}",
                            tree_digest(variant),
                            instance.seed,
                        )
                        checks[name] = not record.passed
                    else:
                        self.validator.validate_materialized_instance(
                            Path(snapshot.design_path), variant
                        )
                        checks[name] = False
                except (ValueError, FileNotFoundError):
                    checks[name] = True
            return checks

    def run(self) -> Dict[str, Any]:
        if self.controller._holdout_record is not None:
            registry = HoldoutRegistry(self.evidence)
            commitment_id = registry.commitment_id(
                self.controller.manifest.hidden_commitments
            )
            if registry.state(commitment_id) != "retired":
                registry.transition(
                    self.controller.manifest.epoch_id,
                    self.controller.manifest.hidden_commitments,
                    "retired",
                )
            return self.controller._holdout_record
        if self.controller._audit_public_scores is None:
            raise RuntimeError("public audit population is not frozen")
        material = self._open_private_material()
        identities = self._hidden_identities(material)
        seeds = tuple(material["hidden_seeds"]["generator_seeds"])
        final_round = self.controller.manifest.thresholds.rounds - 1
        eligible_profiles = set(self.controller._audit_public_scores)
        snapshots = [
            item
            for item in self.controller.snapshots
            if item.round_index == final_round
            and f"base:{item.snapshot_id}" in eligible_profiles
        ]
        hidden_scores: Dict[str, float] = {}
        composite_scores: Dict[str, float] = {}
        hidden_records = []
        damage_checks: Dict[str, Dict[str, bool]] = {}
        for snapshot in snapshots:
            accuracies = []
            first_instance = None
            for seed_index, seed in enumerate(seeds):
                instance = self._freeze_hidden_instance(snapshot, int(seed))
                first_instance = first_instance or instance
                for solver in identities:
                    cell = self._run_hidden_cell(
                        snapshot, instance, solver, seed_index
                    )
                    hidden_records.append(to_primitive(cell))
                    if cell.state != CellState.SUCCESS or cell.score is None:
                        raise RuntimeError("incomplete hidden solver panel")
                    accuracies.append(cell.score.accuracy)
            profile_id = f"base:{snapshot.snapshot_id}"
            quality = 1.0 - max(accuracies)
            hidden_scores[profile_id] = quality
            promotion = self.controller.promotions.get(snapshot.design_digest)
            shared = statistics.mean((1.0, 1.0 if promotion is not None else 0.0))
            composite_scores[profile_id] = statistics.mean((quality, shared))
            checks = self._damage_checks(snapshot, first_instance)
            damage_checks[profile_id] = checks
            if not all(checks.values()):
                raise RuntimeError(f"public evaluator missed controlled damage: {profile_id}")
            for category in checks:
                damaged_id = f"damaged:{snapshot.snapshot_id}:{category}"
                hidden_scores[damaged_id] = 0.0
                composite_scores[damaged_id] = 0.0
        hidden_scores["control:public-optimizer"] = 0.0
        composite_scores["control:public-optimizer"] = 0.0
        record = audit_evaluator(
            self.controller.manifest.epoch_id,
            self.controller._audit_public_scores,
            composite_scores,
            hidden_scores,
            self.controller._audit_defect_pairs,
            self.controller.manifest.thresholds,
            self.controller.manifest.hidden_commitments,
            material,
        )
        record["hidden_solver_cells"] = hidden_records
        record["damage_checks"] = damage_checks
        record["target_values"] = {
            "composite": composite_scores,
            "hidden_only": hidden_scores,
        }
        self.evidence.publish_record_idempotent(
            self.controller.manifest.epoch_id, "audit", "holdout", record
        )
        HoldoutRegistry(self.evidence).transition(
            self.controller.manifest.epoch_id,
            self.controller.manifest.hidden_commitments,
            "retired",
        )
        self.controller._holdout_record = record
        self.controller.state.set_phase(self.controller.manifest.epoch_id, "audited")
        return record


def validation_record_time(_record: Any) -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

"""Provider-agnostic orchestration for a complete BBA evaluation epoch."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import shutil
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from bba.audit import DefectPair, audit_evaluator
from bba.errors import PredictionParseFailure, ProviderFailure, SolverTimedOut
from bba.evidence import EvidenceStore, file_digest, read_json
from bba.protocol import (
    CandidateSnapshot,
    CandidateStatus,
    CellState,
    ExperimentManifest,
    EvaluationInstance,
    ModelIdentity,
    PromotionDecision,
    PromotionRecord,
    ReviewFindings,
    ScoreSummary,
    SolverAttempt,
    SolverCell,
    canonical_json,
    digest_json,
    promotion_record_from_mapping,
    solver_cell_from_mapping,
    to_primitive,
    validation_record_from_mapping,
)
from bba.registry import PromotionRegistry
from bba.scoring import CandidateEvaluation, classify_candidate, matrix, rank_creators, rank_solvers
from bba.state import LocalStateStore
from bba.validator import PackageValidator, read_jsonl_strict, validate_answer_rows, validate_item_rows, write_jsonl


class CreatorBackend(Protocol):
    def build(
        self,
        identity: ModelIdentity,
        round_index: int,
        output_dir: Path,
        feedback: Mapping[str, Any],
        parent_package: Optional[Path],
        manifest: ExperimentManifest,
    ) -> None:
        ...


class SolverBackend(Protocol):
    def solve(
        self,
        identity: ModelIdentity,
        solver_bundle: Path,
        items: Sequence[Mapping[str, Any]],
        repetition: int,
        manifest: ExperimentManifest,
    ) -> Sequence[Mapping[str, Any]]:
        ...


class TournamentController:
    def __init__(
        self,
        manifest: ExperimentManifest,
        evidence: EvidenceStore,
        validator: Optional[PackageValidator] = None,
        creator_backends: Optional[Mapping[str, CreatorBackend]] = None,
        solver_backends: Optional[Mapping[str, SolverBackend]] = None,
        state: Optional[LocalStateStore] = None,
    ):
        self.manifest = manifest
        self.evidence = evidence
        self.validator = validator
        self.creator_backends = dict(creator_backends or {})
        self.solver_backends = dict(solver_backends or {})
        self.state = state or LocalStateStore(self.evidence.root / "bba-state.sqlite3")
        self.state.register_epoch(manifest)
        expected = {identity.artifact_id for identity in manifest.cohort}
        if self.creator_backends and set(self.creator_backends) != expected:
            raise ValueError("creator backends must cover the exact frozen cohort")
        if self.solver_backends and set(self.solver_backends) != expected:
            raise ValueError("creator and solver backends must cover the exact frozen cohort")
        if validator is not None and validator.sandbox.backend != manifest.sandbox.backend:
            raise ValueError("validation sandbox does not match the frozen manifest")
        for backend in self.creator_backends.values():
            prompt_digest = getattr(backend, "prompt_digest", None)
            if prompt_digest is not None and prompt_digest != manifest.creator_prompt_digest:
                raise ValueError("creator backend prompt does not match the frozen manifest")
        for backend in self.solver_backends.values():
            prompt_digest = getattr(backend, "prompt_digest", None)
            if prompt_digest is not None and prompt_digest != manifest.solver_prompt_digest:
                raise ValueError("solver backend prompt does not match the frozen manifest")
        self.snapshots: List[CandidateSnapshot] = []
        self.instances: Dict[str, EvaluationInstance] = {}
        self.round_seeds: Dict[int, int] = {}
        self.validations: Dict[str, Any] = {}
        self.cells: Dict[str, List[SolverCell]] = {}
        self.attempts: Dict[str, List[SolverAttempt]] = {}
        self.promotions: Dict[str, PromotionRecord] = {}
        self._public_closed = False
        self._audit_public_scores: Optional[Dict[str, float]] = None
        self._audit_defect_pairs: tuple = ()
        self._holdout_record: Optional[Dict[str, Any]] = None
        self._restore_state()

    def _creator_work_id(self, creator: ModelIdentity, round_index: int) -> str:
        return f"creator--r{round_index}--{creator.artifact_id}"

    def _creator_payload(
        self,
        creator: ModelIdentity,
        round_index: int,
        parent: Optional[CandidateSnapshot],
    ) -> Dict[str, Any]:
        return {
            "manifest_digest": self.manifest.digest,
            "creator": creator.artifact_id,
            "round": round_index,
            "parent_snapshot_id": parent.snapshot_id if parent else None,
        }

    def _validation_work_id(self, snapshot: CandidateSnapshot) -> str:
        return f"validation--{snapshot.snapshot_id}"

    def _validation_payload(self, snapshot: CandidateSnapshot) -> Dict[str, Any]:
        return {
            "manifest_digest": self.manifest.digest,
            "snapshot_id": snapshot.snapshot_id,
            "design_digest": snapshot.design_digest,
            "evaluation_seed": self.round_seeds[snapshot.round_index],
        }

    def _solver_work_id(
        self,
        snapshot: CandidateSnapshot,
        solver: ModelIdentity,
        repetition: int,
    ) -> str:
        return f"solver--{self._cell_record_id(snapshot, solver, repetition)}"

    def _solver_payload(
        self,
        snapshot: CandidateSnapshot,
        solver: ModelIdentity,
        repetition: int,
    ) -> Dict[str, Any]:
        return {
            "epoch_digest": self.manifest.digest,
            "snapshot_id": snapshot.snapshot_id,
            "instance_digest": self.instances[snapshot.snapshot_id].instance_digest,
            "solver": to_primitive(solver),
            "repetition": repetition,
            "budget": to_primitive(self.manifest.budget),
        }

    def _evidence_ref(self, path: Path) -> str:
        return path.resolve().relative_to(self.evidence.root).as_posix()

    def _ordered_snapshots(self, snapshots: Sequence[CandidateSnapshot]) -> List[CandidateSnapshot]:
        cohort_order = {
            identity.artifact_id: index for index, identity in enumerate(self.manifest.cohort)
        }
        return sorted(
            snapshots,
            key=lambda item: (
                item.round_index,
                cohort_order.get(item.creator.artifact_id, len(cohort_order)),
            ),
        )

    def _restore_state(self) -> None:
        manifest_path = self.evidence.epoch_root(self.manifest.epoch_id) / "manifest.json"
        if not manifest_path.exists():
            return
        frozen = self.evidence.load_manifest(self.manifest.epoch_id)
        if frozen.digest != self.manifest.digest:
            raise ValueError("evidence manifest does not match the requested manifest")

        self.snapshots = self._ordered_snapshots(
            self.evidence.load_snapshots(self.manifest.epoch_id)
        )
        snapshot_keys = set()
        snapshot_by_id = {item.snapshot_id: item for item in self.snapshots}
        for snapshot in self.snapshots:
            key = (snapshot.creator.artifact_id, snapshot.round_index)
            if key in snapshot_keys:
                raise ValueError(f"multiple snapshots exist for creator round: {key}")
            snapshot_keys.add(key)
            parent = snapshot_by_id.get(snapshot.parent_snapshot_id)
            if snapshot.round_index == 0 and snapshot.parent_snapshot_id is not None:
                raise ValueError("round-zero snapshot cannot have a parent")
            if snapshot.round_index > 0 and (
                parent is None
                or parent.creator != snapshot.creator
                or parent.round_index != snapshot.round_index - 1
            ):
                raise ValueError(f"candidate revision chain is invalid: {snapshot.snapshot_id}")
            payload = self._creator_payload(snapshot.creator, snapshot.round_index, parent)
            metadata_path = (
                self.evidence.epoch_root(self.manifest.epoch_id)
                / "candidates"
                / snapshot.snapshot_id
                / "snapshot.json"
            )
            self.state.reconcile_success(
                self.manifest.epoch_id,
                self._creator_work_id(snapshot.creator, snapshot.round_index),
                "creator",
                payload,
                self._evidence_ref(metadata_path),
                file_digest(metadata_path),
            )

        seed_root = self.evidence.epoch_root(self.manifest.epoch_id) / "round-seeds"
        for path in sorted(seed_root.glob("round-*.json")):
            value = read_json(path)
            round_index = int(value["round"])
            seed = int(value["seed"])
            if value.get("manifest_digest") != self.manifest.digest:
                raise ValueError(f"round seed manifest mismatch: {path.name}")
            expected_snapshot_ids = sorted(
                snapshot.snapshot_id
                for snapshot in self.snapshots
                if snapshot.round_index == round_index
            )
            if value.get("snapshot_ids") != expected_snapshot_ids:
                raise ValueError(f"round seed snapshot set mismatch: {path.name}")
            self.round_seeds[round_index] = seed

        for instance in self.evidence.load_instances(self.manifest.epoch_id):
            snapshot = snapshot_by_id.get(instance.snapshot_id)
            if snapshot is None or instance.design_digest != snapshot.design_digest:
                raise ValueError(f"evaluation instance has no matching design: {instance.instance_id}")
            if self.round_seeds.get(snapshot.round_index) != instance.seed:
                raise ValueError(f"evaluation instance has the wrong round seed: {instance.instance_id}")
            if snapshot.snapshot_id in self.instances:
                raise ValueError(f"multiple evaluation instances exist: {snapshot.snapshot_id}")
            self.instances[snapshot.snapshot_id] = instance

        validation_root = self.evidence.epoch_root(self.manifest.epoch_id) / "validations"
        for path in sorted(validation_root.glob("*.json")):
            snapshot_id = path.stem
            snapshot = next(
                (item for item in self.snapshots if item.snapshot_id == snapshot_id), None
            )
            if snapshot is None:
                raise ValueError(f"validation has no candidate snapshot: {snapshot_id}")
            record = validation_record_from_mapping(read_json(path))
            instance = self.instances.get(snapshot_id)
            if (
                record.snapshot_id != snapshot_id
                or record.design_digest != snapshot.design_digest
            ):
                raise ValueError(f"validation digest mismatch: {snapshot_id}")
            if record.passed and (
                instance is None or record.instance_digest != instance.instance_digest
            ):
                raise ValueError(f"passed validation has no matching instance: {snapshot_id}")
            if not record.passed and instance is not None:
                raise ValueError(f"invalid design has an evaluation instance: {snapshot_id}")
            self.validations[snapshot_id] = record
            self.state.reconcile_success(
                self.manifest.epoch_id,
                self._validation_work_id(snapshot),
                "validation",
                self._validation_payload(snapshot),
                self._evidence_ref(path),
                file_digest(path),
            )

        for attempt in self.evidence.load_solver_attempts(self.manifest.epoch_id):
            self.attempts.setdefault(attempt.cell_id, []).append(attempt)
        for cell_id in self.attempts:
            self.attempts[cell_id].sort(key=lambda item: item.attempt_index)
            indexes = [item.attempt_index for item in self.attempts[cell_id]]
            if indexes != list(range(1, len(indexes) + 1)):
                raise ValueError(f"solver attempt sequence is incomplete: {cell_id}")

        cell_root = self.evidence.epoch_root(self.manifest.epoch_id) / "solver-cells"
        for path in sorted(cell_root.glob("*.json")):
            cell = solver_cell_from_mapping(read_json(path))
            matches = [
                snapshot
                for snapshot in self.snapshots
                if snapshot.snapshot_id == cell.snapshot_id
                and self.instances[snapshot.snapshot_id].instance_digest == cell.instance_digest
                and path.name
                == f"{self._cell_record_id(snapshot, cell.solver, cell.repetition)}.json"
            ]
            if len(matches) != 1:
                raise ValueError(f"solver cell path does not match its identity: {path.name}")
            snapshot = matches[0]
            expected_invocation = digest_json(
                self._solver_payload(snapshot, cell.solver, cell.repetition)
            )
            if cell.invocation_digest != expected_invocation:
                raise ValueError(f"solver cell invocation digest is invalid: {path.name}")
            attempts = self.attempts.get(path.stem, [])
            if tuple(item.attempt_id for item in attempts) != cell.attempt_ids:
                raise ValueError(f"solver cell attempts do not match immutable evidence: {path.name}")
            selected = next(
                (item for item in attempts if item.attempt_id == cell.selected_attempt_id),
                None,
            )
            if selected is None or (
                selected.state != cell.state
                or selected.score != cell.score
                or selected.prediction_digest != cell.prediction_digest
                or selected.per_item != cell.per_item
                or selected.error != cell.error
            ):
                raise ValueError(f"solver cell selection is invalid: {path.name}")
            self.cells.setdefault(snapshot.snapshot_id, []).append(cell)
            self.state.reconcile_success(
                self.manifest.epoch_id,
                self._solver_work_id(snapshot, cell.solver, cell.repetition),
                "solver",
                self._solver_payload(snapshot, cell.solver, cell.repetition),
                self._evidence_ref(path),
                file_digest(path),
            )
        for snapshot_id in self.cells:
            self.cells[snapshot_id].sort(
                key=lambda item: (item.solver.artifact_id, item.repetition)
            )

        promotion_root = self.evidence.epoch_root(self.manifest.epoch_id) / "promotions"
        for path in sorted(promotion_root.glob("*.json")):
            record = promotion_record_from_mapping(read_json(path))
            if record.decision == PromotionDecision.APPROVED:
                existing = self.promotions.get(record.design_digest)
                if existing is not None and existing != record:
                    raise ValueError(
                        f"multiple approved promotion records exist: {record.design_digest}"
                    )
                self.promotions[record.design_digest] = record

        audit_population_path = self.evidence.record_path(
            self.manifest.epoch_id, "audit", "public-population"
        )
        if audit_population_path.exists():
            population = read_json(audit_population_path)
            self._audit_public_scores = {
                str(key): float(value)
                for key, value in population["public_scores"].items()
            }
            self._audit_defect_pairs = tuple(
                DefectPair(**item) for item in population["defect_pairs"]
            )
            self.state.set_phase(self.manifest.epoch_id, "audit_population_frozen")

        public_path = self.evidence.record_path(
            self.manifest.epoch_id, "evaluation", "public"
        )
        if public_path.exists():
            self._public_closed = True
            self.state.set_phase(self.manifest.epoch_id, "public_closed")

        holdout_path = self.evidence.record_path(
            self.manifest.epoch_id, "audit", "holdout"
        )
        if holdout_path.exists():
            self._holdout_record = read_json(holdout_path)
            self.state.set_phase(self.manifest.epoch_id, "audited")

        if self._public_run_is_complete() and not self._public_closed:
            self.state.set_phase(self.manifest.epoch_id, "awaiting_review")

    def _public_run_is_complete(self) -> bool:
        expected_snapshots = len(self.manifest.cohort) * self.manifest.thresholds.rounds
        if len(self.snapshots) != expected_snapshots:
            return False
        for snapshot in self.snapshots:
            validation = self.validations.get(snapshot.snapshot_id)
            if validation is None:
                return False
            if validation.passed:
                instance = self.instances.get(snapshot.snapshot_id)
                if (
                    instance is None
                    or validation.instance_digest != instance.instance_digest
                ):
                    return False
                expected_cells = (
                    len(self.manifest.cohort)
                    * self.manifest.thresholds.solver_repetitions
                )
                if len(self.cells.get(snapshot.snapshot_id, ())) != expected_cells:
                    return False
        return True

    def epoch_status(self) -> Dict[str, Any]:
        result = self.state.status(self.manifest.epoch_id)
        result.update(
            {
                "snapshots": len(self.snapshots),
                "instances": len(self.instances),
                "validations": len(self.validations),
                "solver_cells": sum(len(items) for items in self.cells.values()),
                "promotions": len(self.promotions),
                "public_closed": self._public_closed,
                "holdout_complete": self._holdout_record is not None,
            }
        )
        return result

    def snapshot_by_id(self, snapshot_id: str) -> CandidateSnapshot:
        for snapshot in self.snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        raise KeyError(f"candidate snapshot does not exist: {snapshot_id}")

    def _freeze_round_seed(self, round_index: int) -> int:
        existing = self.round_seeds.get(round_index)
        if (
            existing is not None
            and existing.decision == decision
            and existing.prior_review_digest == prior_review_digest
        ):
            return existing
        round_snapshots = [
            snapshot for snapshot in self.snapshots if snapshot.round_index == round_index
        ]
        if len(round_snapshots) != len(self.manifest.cohort):
            raise RuntimeError("BBA selects a round seed only after every design is frozen")
        seed = secrets.randbits(63)
        record = {
            "schema_version": 1,
            "manifest_digest": self.manifest.digest,
            "round": round_index,
            "seed": seed,
            "snapshot_ids": sorted(snapshot.snapshot_id for snapshot in round_snapshots),
        }
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id, "round-seeds", f"round-{round_index}", record
        )
        self.round_seeds[round_index] = seed
        return seed

    def _publish_agent_trace(self, record_id: str, backend: Any) -> None:
        take_trace = getattr(backend, "take_trace", None)
        if take_trace is None:
            return
        trace = take_trace()
        if trace is not None:
            self.evidence.publish_attempt_record(
                self.manifest.epoch_id,
                "agent-traces",
                record_id,
                trace,
            )

    def _cell_record_id(self, snapshot: CandidateSnapshot, solver: ModelIdentity, repetition: int) -> str:
        return f"{snapshot.snapshot_id}--{solver.artifact_id}--r{repetition}"

    def _attempt_paths(self, attempt_id: str) -> Dict[str, str]:
        base = (
            self.evidence.epoch_root(self.manifest.epoch_id)
            / "solver-attempts"
            / attempt_id
            / "artifacts"
        )
        return {
            name: (base / filename).relative_to(self.evidence.root).as_posix()
            for name, filename in {
                "predictions": "predictions.jsonl",
                "candidate_scorer_report": "candidate-scorer-report.json",
                "controller_scorer_report": "controller-scorer-report.json",
                "command_result": "command-result.json",
            }.items()
        }

    def _run_solver_attempt(
        self,
        snapshot: CandidateSnapshot,
        solver: ModelIdentity,
        repetition: int,
        attempt_index: int,
    ) -> SolverAttempt:
        design = Path(snapshot.design_path)
        instance = self.instances[snapshot.snapshot_id]
        instance_root = Path(instance.instance_path)
        invocation = self._solver_payload(snapshot, solver, repetition)
        invocation_digest = digest_json(invocation)
        cell_id = self._cell_record_id(snapshot, solver, repetition)
        attempt_id = f"{cell_id}--attempt-{attempt_index}"
        started_at = datetime.now(timezone.utc).isoformat()
        artifacts: Dict[str, Path] = {}
        try:
            with tempfile.TemporaryDirectory(prefix="bba-solver-cell-") as temporary:
                cell_root = Path(temporary)
                public_bundle = cell_root / "solver_bundle"
                shutil.copytree(instance_root / "solver_bundle", public_bundle)
                items = read_jsonl_strict(public_bundle / "items_private_sample.jsonl")
                item_ids = validate_item_rows(items, self.manifest.thresholds.sample_count)
                backend = self.solver_backends[solver.artifact_id]
                try:
                    predictions = list(backend.solve(
                        solver,
                        public_bundle,
                        items,
                        repetition,
                        self.manifest,
                    ))
                finally:
                    self._publish_agent_trace(
                        attempt_id,
                        backend,
                    )
                prediction_ids = validate_answer_rows(
                    predictions,
                    self.manifest.thresholds.sample_count,
                    expected_ids=item_ids,
                )
                prediction_path = cell_root / "predictions.jsonl"
                write_jsonl(prediction_path, predictions)
                prediction_digest = hashlib.sha256(prediction_path.read_bytes()).hexdigest()

                # Creator scorer runs in a fresh generated-code sandbox.  The
                # controller independently recomputes exact matches as a check.
                score_workspace = cell_root / "score_workspace"
                score_package = score_workspace / "candidate"
                shutil.copytree(design, score_package)
                shutil.copytree(
                    instance_root / "solver_bundle",
                    score_package / "solver_bundle",
                    dirs_exist_ok=True,
                )
                shutil.copy2(instance_root / "gold_private_sample.jsonl", score_package)
                shutil.copy2(prediction_path, score_package / "predictions.jsonl")
                score_output = score_package / ".controller_solver_score.json"
                result = self.validator.sandbox.run_python(
                    score_package / "scorer.py",
                    [
                        "--gold", "gold_private_sample.jsonl",
                        "--predictions", "predictions.jsonl",
                        "--out", score_output.name,
                    ],
                    workspace=score_workspace,
                    cwd=score_package,
                    timeout_seconds=self.manifest.budget.solver_seconds,
                )
                if result.returncode != 0 or not score_output.is_file():
                    raise RuntimeError(result.stderr[-1000:] or "creator scorer did not publish a report")
                reported = json.loads(score_output.read_text(encoding="utf-8"))

                gold = read_jsonl_strict(instance_root / "gold_private_sample.jsonl")
                gold_map = {row["id"]: row["answer"] for row in gold}
                pred_map = {row["id"]: row["answer"] for row in predictions}
                per_item = {
                    item_id: canonical_json(pred_map[item_id]) == canonical_json(gold_map[item_id])
                    for item_id in sorted(gold_map)
                }
                correct = sum(per_item.values())
                summary = ScoreSummary(
                    total=len(gold_map),
                    correct=correct,
                    accuracy=correct / len(gold_map),
                )
                reported_summary = ScoreSummary(
                    total=reported.get("total"),
                    correct=reported.get("correct"),
                    accuracy=reported.get("accuracy"),
                    schema_version=reported.get("schema_version"),
                )
                if reported_summary != summary:
                    raise RuntimeError("creator scorer disagrees with controller exact-match score")
                controller_report = cell_root / "controller-scorer-report.json"
                controller_report.write_bytes(canonical_json({
                    "schema_version": 2,
                    "total": summary.total,
                    "correct": summary.correct,
                    "accuracy": summary.accuracy,
                    "per_item": per_item,
                }) + b"\n")
                command_result = cell_root / "command-result.json"
                command_result.write_bytes(canonical_json({
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "timed_out": result.timed_out,
                }) + b"\n")
                artifacts = {
                    "predictions": prediction_path,
                    "candidate_scorer_report": score_output,
                    "controller_scorer_report": controller_report,
                    "command_result": command_result,
                }
                evidence_files = self._attempt_paths(attempt_id)
                evidence_digests = {
                    name: file_digest(path) for name, path in artifacts.items()
                }
                attempt = SolverAttempt(
                    attempt_id=attempt_id,
                    cell_id=cell_id,
                    attempt_index=attempt_index,
                    state=CellState.SUCCESS,
                    invocation_digest=invocation_digest,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    score=summary,
                    prediction_digest=prediction_digest,
                    per_item=per_item,
                    evidence_files=evidence_files,
                    evidence_digests=evidence_digests,
                )
                self.evidence.freeze_solver_attempt(
                    self.manifest.epoch_id, attempt, artifacts
                )
                return attempt
        except SolverTimedOut as exc:
            state, error = CellState.TIMEOUT, str(exc)
        except ProviderFailure as exc:
            state, error = CellState.PROVIDER_ERROR, str(exc)
        except PredictionParseFailure as exc:
            state, error = CellState.PARSE_ERROR, str(exc)
        except ValueError as exc:
            message = str(exc)
            state = CellState.PARTIAL_PREDICTIONS if "expected" in message or "IDs" in message else CellState.PARSE_ERROR
            error = message
        except Exception as exc:
            state, error = CellState.SCORER_ERROR, str(exc)
        with tempfile.TemporaryDirectory(prefix="bba-solver-failure-") as temporary:
            error_path = Path(temporary) / "error.json"
            error_path.write_bytes(canonical_json({"state": state.value, "error": error}) + b"\n")
            evidence_file = (
                self.evidence.epoch_root(self.manifest.epoch_id)
                / "solver-attempts"
                / attempt_id
                / "artifacts"
                / "error.json"
            ).relative_to(self.evidence.root).as_posix()
            attempt = SolverAttempt(
                attempt_id=attempt_id,
                cell_id=cell_id,
                attempt_index=attempt_index,
                state=state,
                invocation_digest=invocation_digest,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                evidence_files={"error": evidence_file},
                evidence_digests={"error": file_digest(error_path)},
                error=error,
            )
            self.evidence.freeze_solver_attempt(
                self.manifest.epoch_id, attempt, {"error": error_path}
            )
            return attempt

    def _select_solver_cell(
        self,
        snapshot: CandidateSnapshot,
        solver: ModelIdentity,
        repetition: int,
        attempts: Sequence[SolverAttempt],
    ) -> SolverCell:
        successful = [item for item in attempts if item.state == CellState.SUCCESS]
        selected = successful[0] if successful else attempts[-1]
        return SolverCell(
            snapshot_id=snapshot.snapshot_id,
            instance_digest=self.instances[snapshot.snapshot_id].instance_digest,
            solver=solver,
            repetition=repetition,
            state=selected.state,
            invocation_digest=selected.invocation_digest,
            attempt_ids=tuple(item.attempt_id for item in attempts),
            selected_attempt_id=selected.attempt_id,
            score=selected.score,
            prediction_digest=selected.prediction_digest,
            per_item=selected.per_item,
            error=selected.error,
        )

    def _feedback(self, snapshot: CandidateSnapshot) -> Dict[str, Any]:
        validation = self.validations[snapshot.snapshot_id]
        cells = self.cells.get(snapshot.snapshot_id, [])
        return {
            "schema_version": 1,
            "source": "public_epoch_evidence_only",
            "snapshot_id": snapshot.snapshot_id,
            "validation": to_primitive(validation),
            "solver_cells": [
                {
                    "solver": cell.solver.artifact_id,
                    "repetition": cell.repetition,
                    "state": cell.state.value,
                    "score": to_primitive(cell.score) if cell.score else None,
                    "per_item": dict(cell.per_item),
                    "error": cell.error,
                }
                for cell in cells
            ],
        }

    def run_public_epoch(self) -> None:
        expected = {identity.artifact_id for identity in self.manifest.cohort}
        if self.validator is None:
            raise RuntimeError("public epoch execution requires a package validator")
        if set(self.creator_backends) != expected or set(self.solver_backends) != expected:
            raise RuntimeError("public epoch execution requires the exact frozen backend cohort")
        if self._public_closed:
            return
        self.evidence.freeze_manifest(self.manifest)
        self.state.set_phase(self.manifest.epoch_id, "public_running")
        snapshot_by_key = {
            (snapshot.creator.artifact_id, snapshot.round_index): snapshot
            for snapshot in self.snapshots
        }
        for round_index in range(self.manifest.thresholds.rounds):
            for creator in self.manifest.cohort:
                parent = snapshot_by_key.get((creator.artifact_id, round_index - 1))
                feedback = self._feedback(parent) if parent is not None else {}
                snapshot = snapshot_by_key.get((creator.artifact_id, round_index))
                if snapshot is not None:
                    continue
                work_id = self._creator_work_id(creator, round_index)
                payload = self._creator_payload(creator, round_index, parent)
                if not self.state.claim(
                    self.manifest.epoch_id, work_id, "creator", payload
                ):
                    raise RuntimeError(
                        f"creator work is complete but snapshot evidence is missing: {work_id}"
                    )
                backend = self.creator_backends[creator.artifact_id]
                with tempfile.TemporaryDirectory(prefix="bba-creator-output-") as temporary:
                    output = Path(temporary) / "design"
                    output.mkdir()
                    try:
                        backend.build(
                            creator,
                            round_index,
                            output,
                            feedback,
                            Path(parent.design_path) if parent else None,
                            self.manifest,
                        )
                    except Exception as exc:
                        self.state.fail(self.manifest.epoch_id, work_id, str(exc))
                        self._publish_agent_trace(
                            f"{creator.artifact_id}--round-{round_index}--failed",
                            backend,
                        )
                        raise
                    snapshot = self.evidence.freeze_candidate(
                        self.manifest.epoch_id,
                        output,
                        creator,
                        round_index,
                        parent_snapshot_id=parent.snapshot_id if parent else None,
                    )
                    self._publish_agent_trace(
                        f"{snapshot.snapshot_id}--creator",
                        backend,
                    )
                metadata_path = (
                    self.evidence.epoch_root(self.manifest.epoch_id)
                    / "candidates"
                    / snapshot.snapshot_id
                    / "snapshot.json"
                )
                self.state.succeed(
                    self.manifest.epoch_id,
                    work_id,
                    self._evidence_ref(metadata_path),
                    file_digest(metadata_path),
                )
                self.snapshots.append(snapshot)
                self.snapshots = self._ordered_snapshots(self.snapshots)
                snapshot_by_key[(creator.artifact_id, round_index)] = snapshot

            seed = self._freeze_round_seed(round_index)
            round_snapshots = [
                snapshot for snapshot in self.snapshots
                if snapshot.round_index == round_index
            ]
            for snapshot in round_snapshots:
                validation = self.validations.get(snapshot.snapshot_id)
                if validation is None:
                    validation_work_id = self._validation_work_id(snapshot)
                    validation_payload = self._validation_payload(snapshot)
                    if not self.state.claim(
                        self.manifest.epoch_id,
                        validation_work_id,
                        "validation",
                        validation_payload,
                    ):
                        raise RuntimeError(
                            "validation work is complete but evidence is missing: "
                            + validation_work_id
                        )
                    try:
                        with tempfile.TemporaryDirectory(
                            prefix="bba-instance-materialize-"
                        ) as temporary:
                            generated_output = Path(temporary) / "instance"
                            validation = self.validator.validate(
                                Path(snapshot.design_path),
                                snapshot.snapshot_id,
                                snapshot.design_digest,
                                seed,
                                generated_output,
                            )
                            if validation.passed:
                                instance = self.evidence.freeze_instance(
                                    self.manifest.epoch_id,
                                    generated_output,
                                    snapshot,
                                    seed,
                                    self.manifest.thresholds.sample_count,
                                )
                                if validation.instance_digest != instance.instance_digest:
                                    raise RuntimeError(
                                        "validation and frozen instance digests disagree"
                                    )
                                self.instances[snapshot.snapshot_id] = instance
                    except Exception as exc:
                        self.state.fail(
                            self.manifest.epoch_id, validation_work_id, str(exc)
                        )
                        raise
                    validation_path = self.evidence.publish_record_idempotent(
                        self.manifest.epoch_id,
                        "validations",
                        snapshot.snapshot_id,
                        validation,
                    )
                    self.state.succeed(
                        self.manifest.epoch_id,
                        validation_work_id,
                        self._evidence_ref(validation_path),
                        file_digest(validation_path),
                    )
                    self.validations[snapshot.snapshot_id] = validation

                cells = self.cells.setdefault(snapshot.snapshot_id, [])
                existing_cells = {
                    (cell.solver.artifact_id, cell.repetition): cell for cell in cells
                }
                if validation.passed:
                    if snapshot.snapshot_id not in self.instances:
                        raise RuntimeError(
                            "valid design has no frozen evaluation instance: "
                            + snapshot.snapshot_id
                        )
                    for solver in self.manifest.cohort:
                        for repetition in range(
                            self.manifest.thresholds.solver_repetitions
                        ):
                            cell_key = (solver.artifact_id, repetition)
                            if cell_key in existing_cells:
                                continue
                            solver_work_id = self._solver_work_id(
                                snapshot, solver, repetition
                            )
                            solver_payload = self._solver_payload(
                                snapshot, solver, repetition
                            )
                            if not self.state.claim(
                                self.manifest.epoch_id,
                                solver_work_id,
                                "solver",
                                solver_payload,
                            ):
                                raise RuntimeError(
                                    "solver work is complete but evidence is missing: "
                                    + solver_work_id
                                )
                            cell_id = self._cell_record_id(
                                snapshot, solver, repetition
                            )
                            attempts = list(self.attempts.get(cell_id, ()))
                            while True:
                                if attempts and attempts[-1].state == CellState.SUCCESS:
                                    break
                                if attempts and attempts[-1].state.value not in set(
                                    self.manifest.retry_policy.retryable_states
                                ):
                                    break
                                if len(attempts) >= self.manifest.retry_policy.max_attempts:
                                    break
                                attempt = self._run_solver_attempt(
                                    snapshot,
                                    solver,
                                    repetition,
                                    len(attempts) + 1,
                                )
                                attempts.append(attempt)
                                self.attempts[cell_id] = list(attempts)
                            if not attempts:
                                raise RuntimeError(
                                    f"solver cell has no immutable attempt: {cell_id}"
                                )
                            cell = self._select_solver_cell(
                                snapshot, solver, repetition, attempts
                            )
                            cell_path = self.evidence.publish_record_idempotent(
                                self.manifest.epoch_id,
                                "solver-cells",
                                self._cell_record_id(snapshot, solver, repetition),
                                cell,
                            )
                            self.state.succeed(
                                self.manifest.epoch_id,
                                solver_work_id,
                                self._evidence_ref(cell_path),
                                file_digest(cell_path),
                            )
                            cells.append(cell)
                            existing_cells[cell_key] = cell

            self.evidence.publish_record_idempotent(
                self.manifest.epoch_id,
                "state",
                f"round-{round_index}-complete",
                {
                    "manifest_digest": self.manifest.digest,
                    "round": round_index,
                    "evaluation_seed": seed,
                },
            )
        if not self._public_run_is_complete():
            raise RuntimeError("public epoch stopped before all required work was complete")
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id,
            "state",
            "public-run-complete",
            {"manifest_digest": self.manifest.digest},
        )
        self.state.set_phase(self.manifest.epoch_id, "awaiting_review")

    def select_review_items(self, snapshot: CandidateSnapshot) -> List[str]:
        instance = self.instances[snapshot.snapshot_id]
        gold = read_jsonl_strict(Path(instance.instance_path) / "gold_private_sample.jsonl")
        ids = sorted(row["id"] for row in gold)
        generator = random.Random(int(instance.instance_digest[:16], 16))
        return sorted(generator.sample(ids, self.manifest.thresholds.reviewer_sample_count))

    def record_human_review(
        self,
        snapshot: CandidateSnapshot,
        reviewer_id: str,
        reconstructed_answers: Mapping[str, Any],
        decision: PromotionDecision,
        findings: ReviewFindings,
        limitations: Sequence[str],
        key_id: str,
        signing_key: bytes,
        public_key: bytes,
        prior_review_digest: Optional[str] = None,
    ) -> PromotionRecord:
        if not self._public_run_is_complete():
            raise RuntimeError("human review requires a complete public run")
        if snapshot.round_index != self.manifest.thresholds.rounds - 1:
            raise ValueError("canonical review is limited to final-round snapshots")
        expected_ids = self.select_review_items(snapshot)
        if set(reconstructed_answers) != set(expected_ids):
            raise ValueError("review must reconstruct the controller-selected six-item sample")
        reconstructed_digest = hashlib.sha256(
            canonical_json(dict(reconstructed_answers))
        ).hexdigest()
        registry = PromotionRegistry(self.evidence)
        registry.trust_key(key_id, public_key)
        instance = self.instances[snapshot.snapshot_id]
        existing = self.promotions.get(snapshot.design_digest)
        if existing is not None:
            if not registry.verify(existing):
                raise ValueError("the trusted public key does not verify the existing review")
            requested_fields = (
                reviewer_id,
                decision,
                reconstructed_digest,
                findings,
                tuple(limitations),
                key_id,
                prior_review_digest,
            )
            existing_fields = (
                existing.reviewer_id,
                existing.decision,
                existing.reconstructed_answers_digest,
                existing.findings,
                existing.limitations,
                existing.key_id,
                existing.prior_review_digest,
            )
            if (
                requested_fields != existing_fields
                or existing.sampled_item_ids != tuple(expected_ids)
            ):
                raise ValueError("a different review already exists for this candidate")
            self.evidence.publish_record_idempotent(
                self.manifest.epoch_id,
                "promotions",
                snapshot.design_digest,
                existing,
            )
            target_registry = PromotionRegistry(
                self.evidence, registry_name="promotion-history"
            )
            target_registry.append(existing)
            self.promotions[snapshot.design_digest] = existing
            return existing
        gold = read_jsonl_strict(Path(instance.instance_path) / "gold_private_sample.jsonl")
        gold_map = {row["id"]: row["answer"] for row in gold}
        all_correct = all(
            canonical_json(reconstructed_answers[item_id]) == canonical_json(gold_map[item_id])
            for item_id in expected_ids
        )
        if decision == PromotionDecision.APPROVED and not all_correct:
            raise ValueError("an approved review must reconstruct every sampled answer")
        validation = self.validations.get(snapshot.snapshot_id)
        cells = self.cells.get(snapshot.snapshot_id, ())
        expected_cell_count = (
            len(self.manifest.cohort)
            * self.manifest.thresholds.solver_repetitions
        )
        eligible_status = classify_candidate(
            snapshot,
            validation,
            cells,
            self.manifest.cohort,
            self.manifest.thresholds.solver_repetitions,
            self.manifest.thresholds.rejection_accuracy,
        ).status
        if decision == PromotionDecision.APPROVED:
            if validation is None or not validation.passed:
                raise ValueError("approval requires passed mechanical validation")
            if len(cells) != expected_cell_count or any(
                cell.state != CellState.SUCCESS for cell in cells
            ):
                raise ValueError("approval requires a complete successful solver panel")
            if eligible_status not in {
                CandidateStatus.AWAITING_REVIEW,
                CandidateStatus.SOLVABILITY_AUDIT,
            }:
                raise ValueError(
                    f"candidate status is not eligible for approval: {eligible_status.value}"
                )
            if not findings.all_passed:
                raise ValueError("approval requires every construct-validity finding to pass")
        if decision == PromotionDecision.ESCALATED and prior_review_digest is not None:
            raise ValueError("the first escalated review cannot refer to a prior review")
        if prior_review_digest is not None:
            prior_paths = sorted(
                (self.evidence.epoch_root(self.manifest.epoch_id) / "promotions").glob(
                    f"{snapshot.design_digest}*.json"
                )
            )
            prior_records = [promotion_record_from_mapping(read_json(path)) for path in prior_paths]
            prior = next(
                (item for item in prior_records if digest_json(item) == prior_review_digest),
                None,
            )
            if prior is None or prior.decision != PromotionDecision.ESCALATED:
                raise ValueError("second review must refer to an escalated first review")
            if prior.reviewer_id == reviewer_id or prior.key_id == key_id:
                raise ValueError("second review requires a different reviewer and key")
        validation_path = (
            self.evidence.epoch_root(self.manifest.epoch_id)
            / "validations"
            / f"{snapshot.snapshot_id}.json"
        )
        instance_path = (
            self.evidence.epoch_root(self.manifest.epoch_id)
            / "instances"
            / instance.instance_id
            / "instance.json"
        )
        cell_paths = sorted((self.evidence.epoch_root(self.manifest.epoch_id) / "solver-cells").glob(f"{snapshot.snapshot_id}--*.json"))
        evidence_digests = {
            "validation": file_digest(validation_path),
            "instance": file_digest(instance_path),
        }
        evidence_digests.update({f"cell_{index}": file_digest(path) for index, path in enumerate(cell_paths)})
        record = PromotionRecord(
            design_digest=snapshot.design_digest,
            instance_digest=instance.instance_digest,
            reviewer_id=reviewer_id,
            decision=decision,
            sampled_item_ids=tuple(expected_ids),
            reconstructed_answers_digest=reconstructed_digest,
            findings=findings,
            evidence_digests=evidence_digests,
            limitations=tuple(limitations),
            timestamp=datetime.now(timezone.utc).isoformat(),
            key_id=key_id,
            prior_review_digest=prior_review_digest,
        )
        signed = registry.sign(record, signing_key)
        if not registry.verify(signed):
            raise ValueError("trusted reviewer public key does not match the private key")
        record_id = snapshot.design_digest
        if prior_review_digest is not None or decision == PromotionDecision.ESCALATED:
            record_id = f"{snapshot.design_digest}--{digest_json(signed)[:12]}"
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id,
            "promotions",
            record_id,
            signed,
        )
        target_registry = PromotionRegistry(
            self.evidence, registry_name="promotion-history"
        )
        target_registry.append(signed)
        if signed.decision == PromotionDecision.APPROVED:
            self.promotions[snapshot.design_digest] = signed
        return signed

    def _evaluations(self) -> List[CandidateEvaluation]:
        return [
            classify_candidate(
                snapshot,
                self.validations[snapshot.snapshot_id],
                self.cells.get(snapshot.snapshot_id, []),
                self.manifest.cohort,
                self.manifest.thresholds.solver_repetitions,
                self.manifest.thresholds.rejection_accuracy,
                self.promotions.get(snapshot.design_digest),
            )
            for snapshot in self.snapshots
        ]

    def close_public_epoch(self) -> Dict[str, Any]:
        if self._public_closed:
            record = self.evidence.read_record(
                self.manifest.epoch_id, "evaluation", "public"
            )
            self.evidence.publish_record_idempotent(
                self.manifest.epoch_id,
                "state",
                "public-closed",
                {
                    "manifest_digest": self.manifest.digest,
                    "evaluation_digest": digest_json(record),
                },
            )
            self.state.set_phase(self.manifest.epoch_id, "public_closed")
            return record
        if not self._public_run_is_complete():
            raise RuntimeError("public epoch work is incomplete")
        if self._audit_public_scores is None:
            raise RuntimeError("audit population must be frozen before the public epoch closes")
        evaluations = self._evaluations()
        final_round = self.manifest.thresholds.rounds - 1
        blind = rank_creators(evaluations, 0)
        final = rank_creators(evaluations, final_round)
        blind_by_creator = {row["creator"]: row for row in blind}
        adaptation = []
        for row in final:
            earlier = blind_by_creator[row["creator"]]
            before = earlier["public_quality"]
            after = row["public_quality"]
            adaptation.append({
                "creator": row["creator"],
                "blind_quality": before,
                "final_quality": after,
                "adaptation_gain": after - before if before is not None and after is not None else None,
            })
        record = {
            "schema_version": 1,
            "epoch_id": self.manifest.epoch_id,
            "manifest_digest": self.manifest.digest,
            "matrix": matrix(evaluations, self.manifest.cohort),
            "creator_rankings": {"blind_round": blind, "final_round": final},
            "adaptation": adaptation,
            "solver_ranking": rank_solvers(
                evaluations,
                self.manifest.cohort,
                int(self.manifest.digest[:16], 16),
            ),
            "candidate_statuses": {
                evaluation.snapshot.snapshot_id: evaluation.status.value
                for evaluation in evaluations
            },
            "hidden_evidence_included": False,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id, "evaluation", "public", record
        )
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id,
            "state",
            "public-closed",
            {"manifest_digest": self.manifest.digest, "evaluation_digest": digest_json(record)},
        )
        self._public_closed = True
        self.state.set_phase(self.manifest.epoch_id, "public_closed")
        registry = PromotionRegistry(self.evidence)
        for promotion in sorted(
            self.promotions.values(), key=lambda item: item.design_digest
        ):
            registry.append(promotion)
        return record

    def freeze_audit_population(
        self,
        public_scores: Mapping[str, float],
        defect_pairs: Sequence[DefectPair],
    ) -> Dict[str, Any]:
        """Freeze public evaluator outputs before any holdout is revealed."""

        if self._public_closed:
            raise RuntimeError("audit population must be frozen before public closure")
        if not self._public_run_is_complete():
            raise RuntimeError("audit population requires a complete public run")
        if len(public_scores) < 2:
            raise ValueError("audit population requires at least two profiles")
        normalized = {str(key): float(value) for key, value in public_scores.items()}
        if any(not 0.0 <= value <= 1.0 for value in normalized.values()):
            raise ValueError("public audit scores must be normalized to [0, 1]")
        for pair in defect_pairs:
            if pair.base_id not in normalized or pair.damaged_id not in normalized:
                raise ValueError("defect pairs must refer to frozen public profiles")
        record = {
            "schema_version": 1,
            "epoch_id": self.manifest.epoch_id,
            "public_scores": normalized,
            "defect_pairs": [to_primitive(pair) for pair in defect_pairs],
            "frozen_before_hidden_reveal": True,
        }
        if self._audit_public_scores is not None:
            existing = self.evidence.read_record(
                self.manifest.epoch_id, "audit", "public-population"
            )
            if canonical_json(existing) != canonical_json(record):
                raise ValueError("a different audit population is already frozen")
            return existing
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id, "audit", "public-population", record
        )
        self._audit_public_scores = normalized
        self._audit_defect_pairs = tuple(defect_pairs)
        self.state.set_phase(self.manifest.epoch_id, "audit_population_frozen")
        return record

    def run_holdout_audit(
        self,
        composite_holdout: Mapping[str, float],
        hidden_only_holdout: Mapping[str, float],
        revealed_material: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not self._public_closed:
            raise RuntimeError("holdout evidence cannot be opened before the public epoch closes")
        if self._audit_public_scores is None:
            raise RuntimeError("audit population was not frozen")
        record = audit_evaluator(
            self.manifest.epoch_id,
            self._audit_public_scores,
            composite_holdout,
            hidden_only_holdout,
            self._audit_defect_pairs,
            self.manifest.thresholds,
            self.manifest.hidden_commitments,
            revealed_material,
        )
        if self._holdout_record is not None:
            if canonical_json(self._holdout_record) != canonical_json(record):
                raise ValueError("a different holdout audit is already recorded")
            return self._holdout_record
        self.evidence.publish_record_idempotent(
            self.manifest.epoch_id, "audit", "holdout", record
        )
        self._holdout_record = record
        self.state.set_phase(self.manifest.epoch_id, "audited")
        return record

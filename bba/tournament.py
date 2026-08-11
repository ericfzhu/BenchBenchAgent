"""Provider-agnostic orchestration for a complete BBA evaluation epoch."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from bba.audit import DefectPair, audit_evaluator
from bba.errors import PredictionParseFailure, ProviderFailure, SolverTimedOut
from bba.evidence import EvidenceStore, file_digest
from bba.protocol import (
    CandidateSnapshot,
    CellState,
    ExperimentManifest,
    ModelIdentity,
    PromotionDecision,
    PromotionRecord,
    ScoreSummary,
    SolverCell,
    canonical_json,
    digest_json,
    to_primitive,
)
from bba.registry import PromotionRegistry
from bba.scoring import CandidateEvaluation, classify_candidate, matrix, rank_creators, rank_solvers
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
        validator: PackageValidator,
        creator_backends: Mapping[str, CreatorBackend],
        solver_backends: Mapping[str, SolverBackend],
    ):
        self.manifest = manifest
        self.evidence = evidence
        self.validator = validator
        self.creator_backends = dict(creator_backends)
        self.solver_backends = dict(solver_backends)
        expected = {identity.artifact_id for identity in manifest.cohort}
        if set(self.creator_backends) != expected or set(self.solver_backends) != expected:
            raise ValueError("creator and solver backends must cover the exact frozen cohort")
        if validator.sandbox.backend != manifest.sandbox.backend:
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
        self.validations: Dict[str, Any] = {}
        self.cells: Dict[str, List[SolverCell]] = {}
        self.promotions: Dict[str, PromotionRecord] = {}
        self._public_closed = False
        self._audit_public_scores: Optional[Dict[str, float]] = None
        self._audit_defect_pairs: tuple = ()

    def _publish_agent_trace(self, record_id: str, backend: Any) -> None:
        take_trace = getattr(backend, "take_trace", None)
        if take_trace is None:
            return
        trace = take_trace()
        if trace is not None:
            self.evidence.publish_record(
                self.manifest.epoch_id,
                "agent-traces",
                record_id,
                trace,
            )

    def _cell_record_id(self, snapshot: CandidateSnapshot, solver: ModelIdentity, repetition: int) -> str:
        return f"{snapshot.snapshot_id}--{solver.artifact_id}--r{repetition}"

    def _run_solver_cell(
        self,
        snapshot: CandidateSnapshot,
        solver: ModelIdentity,
        repetition: int,
    ) -> SolverCell:
        package = Path(snapshot.package_path)
        invocation = {
            "epoch_digest": self.manifest.digest,
            "candidate_digest": snapshot.package_digest,
            "solver": to_primitive(solver),
            "repetition": repetition,
            "budget": to_primitive(self.manifest.budget),
        }
        invocation_digest = digest_json(invocation)
        try:
            with tempfile.TemporaryDirectory(prefix="bba-solver-cell-") as temporary:
                cell_root = Path(temporary)
                public_bundle = cell_root / "solver_bundle"
                shutil.copytree(package / "solver_bundle", public_bundle)
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
                        self._cell_record_id(snapshot, solver, repetition),
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
                shutil.copytree(package, score_package)
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

                gold = read_jsonl_strict(package / "gold_private_sample.jsonl")
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
                return SolverCell(
                    candidate_digest=snapshot.package_digest,
                    solver=solver,
                    repetition=repetition,
                    state=CellState.SUCCESS,
                    invocation_digest=invocation_digest,
                    score=summary,
                    prediction_digest=prediction_digest,
                    per_item=per_item,
                )
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
        return SolverCell(
            candidate_digest=snapshot.package_digest,
            solver=solver,
            repetition=repetition,
            state=state,
            invocation_digest=invocation_digest,
            error=error,
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
        if self.snapshots:
            raise RuntimeError("a controller instance can run its public epoch only once")
        self.evidence.freeze_manifest(self.manifest)
        parents: Dict[str, CandidateSnapshot] = {}
        feedback: Dict[str, Mapping[str, Any]] = {identity.artifact_id: {} for identity in self.manifest.cohort}
        for round_index in range(self.manifest.thresholds.rounds):
            for creator in self.manifest.cohort:
                with tempfile.TemporaryDirectory(prefix="bba-creator-output-") as temporary:
                    output = Path(temporary) / "candidate"
                    output.mkdir()
                    parent = parents.get(creator.artifact_id)
                    backend = self.creator_backends[creator.artifact_id]
                    try:
                        backend.build(
                            creator,
                            round_index,
                            output,
                            feedback[creator.artifact_id],
                            Path(parent.package_path) if parent else None,
                            self.manifest,
                        )
                    except Exception:
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
                self.snapshots.append(snapshot)
                parents[creator.artifact_id] = snapshot
                validation = self.validator.validate(
                    Path(snapshot.package_path),
                    snapshot.package_digest,
                    self.manifest.public_seed,
                )
                self.validations[snapshot.snapshot_id] = validation
                self.evidence.publish_record(
                    self.manifest.epoch_id,
                    "validations",
                    snapshot.snapshot_id,
                    validation,
                )
                cells: List[SolverCell] = []
                if validation.passed:
                    for solver in self.manifest.cohort:
                        for repetition in range(self.manifest.thresholds.solver_repetitions):
                            cell = self._run_solver_cell(snapshot, solver, repetition)
                            cells.append(cell)
                            self.evidence.publish_record(
                                self.manifest.epoch_id,
                                "solver-cells",
                                self._cell_record_id(snapshot, solver, repetition),
                                cell,
                            )
                self.cells[snapshot.snapshot_id] = cells
                feedback[creator.artifact_id] = self._feedback(snapshot)

    def select_review_items(self, snapshot: CandidateSnapshot) -> List[str]:
        gold = read_jsonl_strict(Path(snapshot.package_path) / "gold_private_sample.jsonl")
        ids = sorted(row["id"] for row in gold)
        generator = random.Random(int(snapshot.package_digest[:16], 16))
        return sorted(generator.sample(ids, self.manifest.thresholds.reviewer_sample_count))

    def record_human_review(
        self,
        snapshot: CandidateSnapshot,
        reviewer_id: str,
        reconstructed_answers: Mapping[str, Any],
        decision: PromotionDecision,
        limitations: Sequence[str],
        key_id: str,
        signing_key: bytes,
    ) -> PromotionRecord:
        expected_ids = self.select_review_items(snapshot)
        if set(reconstructed_answers) != set(expected_ids):
            raise ValueError("review must reconstruct the controller-selected six-item sample")
        gold = read_jsonl_strict(Path(snapshot.package_path) / "gold_private_sample.jsonl")
        gold_map = {row["id"]: row["answer"] for row in gold}
        all_correct = all(
            canonical_json(reconstructed_answers[item_id]) == canonical_json(gold_map[item_id])
            for item_id in expected_ids
        )
        if decision == PromotionDecision.APPROVED and not all_correct:
            raise ValueError("an approved review must reconstruct every sampled answer")
        validation_path = (
            self.evidence.epoch_root(self.manifest.epoch_id)
            / "validations"
            / f"{snapshot.snapshot_id}.json"
        )
        cell_paths = sorted((self.evidence.epoch_root(self.manifest.epoch_id) / "solver-cells").glob(f"{snapshot.snapshot_id}--*.json"))
        evidence_digests = {"validation": file_digest(validation_path)}
        evidence_digests.update({f"cell_{index}": file_digest(path) for index, path in enumerate(cell_paths)})
        record = PromotionRecord(
            candidate_digest=snapshot.package_digest,
            reviewer_id=reviewer_id,
            decision=decision,
            sampled_item_ids=tuple(expected_ids),
            reconstructed_answers_digest=hashlib.sha256(canonical_json(dict(reconstructed_answers))).hexdigest(),
            evidence_digests=evidence_digests,
            limitations=tuple(limitations),
            timestamp=datetime.now(timezone.utc).isoformat(),
            key_id=key_id,
        )
        signed = PromotionRegistry.sign(record, signing_key)
        PromotionRegistry(self.evidence).append(signed, signing_key)
        self.promotions[snapshot.package_digest] = signed
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
                self.promotions.get(snapshot.package_digest),
            )
            for snapshot in self.snapshots
        ]

    def close_public_epoch(self) -> Dict[str, Any]:
        if self._public_closed:
            raise RuntimeError("public epoch is already closed")
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
            "solver_ranking": rank_solvers(evaluations, self.manifest.cohort, self.manifest.public_seed),
            "candidate_statuses": {
                evaluation.snapshot.snapshot_id: evaluation.status.value
                for evaluation in evaluations
            },
            "hidden_evidence_included": False,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.evidence.publish_record(self.manifest.epoch_id, "evaluation", "public", record)
        self.evidence.publish_record(
            self.manifest.epoch_id,
            "state",
            "public-closed",
            {"manifest_digest": self.manifest.digest, "evaluation_digest": digest_json(record)},
        )
        self._public_closed = True
        return record

    def freeze_audit_population(
        self,
        public_scores: Mapping[str, float],
        defect_pairs: Sequence[DefectPair],
    ) -> Dict[str, Any]:
        """Freeze public evaluator outputs before any holdout is revealed."""

        if self._public_closed or self._audit_public_scores is not None:
            raise RuntimeError("audit population can be frozen exactly once before public closure")
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
        self.evidence.publish_record(self.manifest.epoch_id, "audit", "public-population", record)
        self._audit_public_scores = normalized
        self._audit_defect_pairs = tuple(defect_pairs)
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
        self.evidence.publish_record(self.manifest.epoch_id, "audit", "holdout", record)
        return record

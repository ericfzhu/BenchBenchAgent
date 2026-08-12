"""Two-sided matrix aggregation, classification, and ranking."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from bba.protocol import (
    CandidateSnapshot,
    CandidateStatus,
    CellState,
    ModelIdentity,
    PromotionDecision,
    PromotionRecord,
    SolverCell,
    ValidationRecord,
    to_primitive,
)


@dataclass(frozen=True)
class SolverAggregate:
    solver: ModelIdentity
    complete: bool
    median_accuracy: Optional[float]
    accuracies: tuple
    item_values: tuple
    states: tuple


@dataclass(frozen=True)
class CandidateEvaluation:
    snapshot: CandidateSnapshot
    validation: ValidationRecord
    cells: tuple
    solver_aggregates: tuple
    status: CandidateStatus
    best_solver_median: Optional[float]
    panel_median: Optional[float]
    public_quality: Optional[float]
    promotion_digest: Optional[str] = None


def aggregate_solver_cells(
    solver: ModelIdentity,
    cells: Sequence[SolverCell],
    repetitions: int,
) -> SolverAggregate:
    ordered = sorted(cells, key=lambda cell: cell.repetition)
    expected = set(range(repetitions))
    complete = (
        len(ordered) == repetitions
        and {cell.repetition for cell in ordered} == expected
        and all(cell.state == CellState.SUCCESS for cell in ordered)
    )
    if not complete:
        return SolverAggregate(
            solver=solver,
            complete=False,
            median_accuracy=None,
            accuracies=(),
            item_values=(),
            states=tuple(cell.state.value for cell in ordered),
        )
    accuracies = tuple(cell.score.accuracy for cell in ordered if cell.score is not None)
    item_values = tuple(
        1.0 if correct else 0.0
        for cell in ordered
        for _item_id, correct in sorted(cell.per_item.items())
    )
    return SolverAggregate(
        solver=solver,
        complete=True,
        median_accuracy=statistics.median(accuracies),
        accuracies=accuracies,
        item_values=item_values,
        states=tuple(cell.state.value for cell in ordered),
    )


def classify_candidate(
    snapshot: CandidateSnapshot,
    validation: ValidationRecord,
    cells: Sequence[SolverCell],
    cohort: Sequence[ModelIdentity],
    repetitions: int,
    rejection_accuracy: float,
    promotion: Optional[PromotionRecord] = None,
) -> CandidateEvaluation:
    aggregates = tuple(
        aggregate_solver_cells(
            solver,
            [cell for cell in cells if cell.solver.artifact_id == solver.artifact_id],
            repetitions,
        )
        for solver in cohort
    )
    if not validation.passed:
        status = CandidateStatus.INVALID
    elif not all(aggregate.complete for aggregate in aggregates):
        status = CandidateStatus.INCOMPLETE
    else:
        medians = [float(aggregate.median_accuracy) for aggregate in aggregates]
        if max(medians) >= rejection_accuracy:
            status = CandidateStatus.TOO_EASY
        elif max(medians) == 0:
            status = CandidateStatus.SOLVABILITY_AUDIT
        elif promotion is None or promotion.decision != PromotionDecision.APPROVED:
            status = CandidateStatus.AWAITING_REVIEW
        elif len(set(medians)) == 1:
            status = CandidateStatus.FRONTIER_CHALLENGE
        else:
            status = CandidateStatus.ACTIVE
    medians = [
        float(aggregate.median_accuracy)
        for aggregate in aggregates
        if aggregate.median_accuracy is not None
    ]
    best = max(medians) if len(medians) == len(cohort) else None
    panel = statistics.median(medians) if len(medians) == len(cohort) else None
    quality = 1.0 - best if best is not None else None
    promotion_digest = None
    if promotion is not None:
        promotion_digest = hashlib.sha256(
            str(to_primitive(promotion)).encode("utf-8")
        ).hexdigest()
        if status == CandidateStatus.SOLVABILITY_AUDIT and promotion.decision == PromotionDecision.APPROVED:
            status = CandidateStatus.FRONTIER_CHALLENGE
    return CandidateEvaluation(
        snapshot=snapshot,
        validation=validation,
        cells=tuple(cells),
        solver_aggregates=aggregates,
        status=status,
        best_solver_median=best,
        panel_median=panel,
        public_quality=quality,
        promotion_digest=promotion_digest,
    )


def rank_creators(evaluations: Sequence[CandidateEvaluation], round_index: int) -> List[Dict[str, Any]]:
    rows = [
        evaluation for evaluation in evaluations
        if evaluation.snapshot.round_index == round_index
    ]
    status_order = {
        CandidateStatus.ACTIVE: 0,
        CandidateStatus.FRONTIER_CHALLENGE: 1,
        CandidateStatus.AWAITING_REVIEW: 2,
        CandidateStatus.SOLVABILITY_AUDIT: 3,
        CandidateStatus.TOO_EASY: 4,
        CandidateStatus.INCOMPLETE: 5,
        CandidateStatus.INVALID: 6,
    }
    rows.sort(key=lambda item: (
        status_order.get(item.status, 99),
        item.best_solver_median if item.best_solver_median is not None else math.inf,
        item.panel_median if item.panel_median is not None else math.inf,
        item.snapshot.creator.artifact_id,
    ))
    result = []
    previous_key = None
    rank = 0
    for index, evaluation in enumerate(rows, 1):
        key = (evaluation.status, evaluation.best_solver_median, evaluation.panel_median)
        rankable = evaluation.status not in {
            CandidateStatus.INCOMPLETE,
            CandidateStatus.INVALID,
        }
        if rankable and key != previous_key:
            rank = index
            previous_key = key
        result.append({
            "rank": rank if rankable else None,
            "creator": evaluation.snapshot.creator.artifact_id,
            "snapshot_id": evaluation.snapshot.snapshot_id,
            "round": round_index,
            "status": evaluation.status.value,
            "best_solver_median": evaluation.best_solver_median,
            "panel_median": evaluation.panel_median,
            "public_quality": evaluation.public_quality,
        })
    return result


def bootstrap_interval(values: Sequence[float], seed: int, samples: int = 2000) -> Tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    generator = random.Random(seed)
    means = []
    for _ in range(samples):
        means.append(statistics.mean(generator.choice(values) for _ in values))
    means.sort()
    low = means[int(0.025 * (samples - 1))]
    high = means[int(0.975 * (samples - 1))]
    return (low, high)


def rank_solvers(
    evaluations: Sequence[CandidateEvaluation],
    cohort: Sequence[ModelIdentity],
    seed: int,
) -> List[Dict[str, Any]]:
    active = [evaluation for evaluation in evaluations if evaluation.status == CandidateStatus.ACTIVE]
    rows = []
    for solver in cohort:
        benchmark_means = []
        item_values = []
        complete = True
        for evaluation in active:
            aggregate = next(
                item for item in evaluation.solver_aggregates
                if item.solver.artifact_id == solver.artifact_id
            )
            if not aggregate.complete:
                complete = False
                break
            benchmark_means.append(float(aggregate.median_accuracy))
            item_values.extend(aggregate.item_values)
        if complete and active:
            score = statistics.mean(benchmark_means)
            low, high = bootstrap_interval(item_values, seed + len(rows))
        else:
            score = low = high = None
        rows.append({
            "solver": solver.artifact_id,
            "complete": complete and bool(active),
            "canonical_benchmarks": len(active),
            "macro_accuracy": score,
            "ci95": [low, high] if score is not None else None,
        })
    rows.sort(key=lambda item: (
        item["macro_accuracy"] is None,
        -(item["macro_accuracy"] or 0.0),
        item["solver"],
    ))
    previous = None
    rank = 0
    for index, row in enumerate(rows, 1):
        if row["macro_accuracy"] != previous:
            rank = index
            previous = row["macro_accuracy"]
        row["rank"] = rank if row["macro_accuracy"] is not None else None
    return rows


def matrix(evaluations: Sequence[CandidateEvaluation], cohort: Sequence[ModelIdentity]) -> Dict[str, Any]:
    return {
        evaluation.snapshot.snapshot_id: {
            aggregate.solver.artifact_id: {
                "complete": aggregate.complete,
                "median_accuracy": aggregate.median_accuracy,
                "repetitions": list(aggregate.accuracies),
                "states": list(aggregate.states),
            }
            for aggregate in evaluation.solver_aggregates
        }
        for evaluation in evaluations
    }

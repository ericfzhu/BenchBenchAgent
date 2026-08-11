"""Decision-level holdout audits for BBA's public evaluator."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from bba.protocol import AuditStatus, DecisionThresholds


@dataclass(frozen=True)
class DefectPair:
    base_id: str
    damaged_id: str
    category: str


def _average_ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            result[indexed[position][0]] = average
        cursor = end
    return result


def spearman(predicted: Sequence[float], truth: Sequence[float]) -> float:
    if len(predicted) != len(truth) or len(predicted) < 2:
        return 0.0
    left = _average_ranks(predicted)
    right = _average_ranks(truth)
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left)
        * sum((b - right_mean) ** 2 for b in right)
    )
    return numerator / denominator if denominator else 0.0


def pairwise_summary(
    predicted: Sequence[float],
    truth: Sequence[float],
    indices: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    selected = list(indices) if indices is not None else list(range(len(predicted)))
    credit = 0.0
    count = 0
    for left_pos, left in enumerate(selected):
        for right in selected[left_pos + 1:]:
            truth_delta = truth[left] - truth[right]
            if truth_delta == 0:
                continue
            predicted_delta = predicted[left] - predicted[right]
            count += 1
            if predicted_delta == 0:
                credit += 0.5
            elif (predicted_delta > 0) == (truth_delta > 0):
                credit += 1.0
    return {"accuracy": credit / count if count else None, "credit": credit, "count": count}


def gap_stratified_pairs(predicted: Sequence[float], truth: Sequence[float]) -> Dict[str, Any]:
    buckets = {"up_to_0.05": [], "0.05_to_0.10": [], "over_0.10": []}
    for left in range(len(predicted)):
        for right in range(left + 1, len(predicted)):
            gap = abs(truth[left] - truth[right])
            if gap == 0:
                continue
            name = "up_to_0.05" if gap <= 0.05 else "0.05_to_0.10" if gap <= 0.10 else "over_0.10"
            buckets[name].append((left, right))
    result = {}
    for name, pairs in buckets.items():
        credit = 0.0
        for left, right in pairs:
            predicted_delta = predicted[left] - predicted[right]
            truth_delta = truth[left] - truth[right]
            if predicted_delta == 0:
                credit += 0.5
            elif (predicted_delta > 0) == (truth_delta > 0):
                credit += 1.0
        result[name] = {
            "accuracy": credit / len(pairs) if pairs else None,
            "credit": credit,
            "count": len(pairs),
        }
    return result


def selection_at_k(predicted: Sequence[float], truth: Sequence[float], ids: Sequence[str], k: int) -> Dict[str, Any]:
    k = max(1, min(k, len(ids)))
    predicted_order = sorted(range(len(ids)), key=lambda index: (-predicted[index], ids[index]))
    truth_order = sorted(range(len(ids)), key=lambda index: (-truth[index], ids[index]))
    selected = predicted_order[:k]
    oracle = truth_order[:k]
    selected_mean = statistics.mean(truth[index] for index in selected)
    oracle_mean = statistics.mean(truth[index] for index in oracle)
    regret = max(0.0, oracle_mean - selected_mean)
    utility = selected_mean / oracle_mean if oracle_mean > 0 else (1.0 if selected_mean == 0 else 0.0)
    overlap = len(set(selected).intersection(oracle))
    return {
        "k": k,
        "regret": regret,
        "utility_recovery": utility,
        "set_recovery": overlap / k,
        "recovered_count": overlap,
        "selected_ids": [ids[index] for index in selected],
        "oracle_ids": [ids[index] for index in oracle],
    }


def defect_summary(
    public: Mapping[str, float],
    truth: Mapping[str, float],
    pairs: Sequence[DefectPair],
) -> Dict[str, Any]:
    credit = 0.0
    count = 0
    by_category: Dict[str, Dict[str, float]] = {}
    for pair in pairs:
        if any(identifier not in public or identifier not in truth for identifier in (pair.base_id, pair.damaged_id)):
            continue
        truth_delta = truth[pair.base_id] - truth[pair.damaged_id]
        if truth_delta == 0:
            continue
        predicted_delta = public[pair.base_id] - public[pair.damaged_id]
        pair_credit = 0.5 if predicted_delta == 0 else 1.0 if (predicted_delta > 0) == (truth_delta > 0) else 0.0
        credit += pair_credit
        count += 1
        category = by_category.setdefault(pair.category, {"credit": 0.0, "count": 0})
        category["credit"] += pair_credit
        category["count"] += 1
    for category in by_category.values():
        category["accuracy"] = category["credit"] / category["count"]
    return {"accuracy": credit / count if count else None, "credit": credit, "count": count, "by_category": by_category}


def metric_vector(
    public: Mapping[str, float],
    truth: Mapping[str, float],
    defect_pairs: Sequence[DefectPair],
) -> Dict[str, Any]:
    ids = sorted(set(public).intersection(truth))
    if len(ids) < 2:
        raise ValueError("a holdout audit requires at least two shared candidate profiles")
    predicted = [float(public[identifier]) for identifier in ids]
    target = [float(truth[identifier]) for identifier in ids]
    ranked = sorted(range(len(ids)), key=lambda index: (-predicted[index], ids[index]))
    top_half = ranked[: max(1, math.ceil(len(ids) / 2))]
    top_quartile_count = max(1, math.ceil(len(ids) / 4))
    top_quartile = ranked[:top_quartile_count]
    all_pairs = pairwise_summary(predicted, target)
    defects = defect_summary(public, truth, defect_pairs)
    selection = selection_at_k(predicted, target, ids, top_quartile_count)
    rho = spearman(predicted, target)
    pair_accuracy = all_pairs["accuracy"] or 0.0
    defect_accuracy = defects["accuracy"] or 0.0
    bbb_v2 = 100 * (
        0.30 * max(0.0, min(1.0, (rho + 1) / 2))
        + 0.20 * pair_accuracy
        + 0.20 * defect_accuracy
        + 0.30 * selection["utility_recovery"]
    )
    return {
        "ids": ids,
        "spearman": rho,
        "pairwise": all_pairs,
        "pairwise_within_public_top_half": pairwise_summary(predicted, target, top_half),
        "pairwise_within_public_top_quartile": pairwise_summary(predicted, target, top_quartile),
        "pairwise_by_target_gap": gap_stratified_pairs(predicted, target),
        "defect_sensitivity": defects,
        "selection_at_quartile": selection,
        "bbb_v2_convenience": bbb_v2,
    }


def audit_evaluator(
    epoch_id: str,
    public_scores: Mapping[str, float],
    composite_holdout: Mapping[str, float],
    hidden_only_holdout: Mapping[str, float],
    defect_pairs: Sequence[DefectPair],
    thresholds: DecisionThresholds,
    commitments: Mapping[str, str],
    revealed_material: Mapping[str, Any],
) -> Dict[str, Any]:
    from bba.protocol import digest_json

    revealed_commitments = {
        key: digest_json(value) for key, value in revealed_material.items()
    }
    if dict(commitments) != revealed_commitments:
        raise ValueError("revealed holdout material does not match the preregistered commitments")
    composite = metric_vector(public_scores, composite_holdout, defect_pairs)
    hidden = metric_vector(public_scores, hidden_only_holdout, defect_pairs)
    defect_accuracy = hidden["defect_sensitivity"]["accuracy"]
    passed = (
        hidden["spearman"] >= thresholds.audit_min_spearman
        and (hidden["pairwise"]["accuracy"] or 0.0) >= thresholds.audit_min_pairwise
        and hidden["selection_at_quartile"]["utility_recovery"] >= thresholds.audit_min_utility_recovery
        and (defect_accuracy or 0.0) >= thresholds.audit_min_defect_sensitivity
    )
    return {
        "schema_version": 1,
        "epoch_id": epoch_id,
        "status": AuditStatus.VALIDATED.value if passed else AuditStatus.UNVALIDATED.value,
        "targets": {
            "composite": composite,
            "hidden_only": hidden,
        },
        "thresholds": {
            "min_spearman": thresholds.audit_min_spearman,
            "min_pairwise": thresholds.audit_min_pairwise,
            "min_utility_recovery": thresholds.audit_min_utility_recovery,
            "min_defect_sensitivity": thresholds.audit_min_defect_sensitivity,
        },
        "shared_components_disclosed": ["mechanical_validity", "human_adjudication"],
        "hidden_components": ["fresh_generator_seeds", "sealed_solver_panel"],
        "holdout_retired": True,
        "revealed_commitments": revealed_commitments,
    }

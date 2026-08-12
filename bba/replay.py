"""Replay stored solver scores without a new model call."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from bba.evidence import EvidenceStore, file_digest, read_json
from bba.protocol import CellState, ScoreSummary, canonical_json, solver_debrief_from_mapping
from bba.validator import read_jsonl_strict, validate_answer_rows


def replay_solver_attempt(
    evidence: EvidenceStore,
    epoch_id: str,
    attempt_id: str,
) -> Dict[str, Any]:
    attempts = {
        attempt.attempt_id: attempt
        for attempt in evidence.load_solver_attempts(epoch_id)
    }
    attempt = attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(f"solver attempt does not exist: {attempt_id}")
    if attempt.state != CellState.SUCCESS:
        raise ValueError("only a successful solver attempt has a replayable score")
    predictions_path = evidence.root / attempt.evidence_files["predictions"]
    if file_digest(predictions_path) != attempt.prediction_digest:
        raise ValueError("stored predictions do not match the prediction digest")
    debrief_path = evidence.root / attempt.evidence_files["debrief"]
    if file_digest(debrief_path) != attempt.debrief_digest:
        raise ValueError("stored debrief does not match the debrief digest")
    debrief = solver_debrief_from_mapping(read_json(debrief_path))
    controller_report = read_json(
        evidence.root / attempt.evidence_files["controller_scorer_report"]
    )
    predictions = read_jsonl_strict(predictions_path)

    instances = evidence.load_instances(epoch_id) + evidence.load_hidden_instances(
        epoch_id
    )
    instance = next(
        (
            item
            for item in instances
            if item.instance_id == attempt.instance_id
        ),
        None,
    )
    if instance is None:
        raise ValueError("solver attempt has no matching frozen instance")
    gold = read_jsonl_strict(
        Path(instance.instance_path) / "gold_private_sample.jsonl"
    )
    gold_ids = {row["id"] for row in gold}
    validate_answer_rows(predictions, len(gold), expected_ids=gold_ids)
    if {item.item_id for item in debrief.items} != gold_ids:
        raise ValueError("stored debrief IDs do not match the frozen instance")
    gold_map = {row["id"]: row["answer"] for row in gold}
    pred_map = {row["id"]: row["answer"] for row in predictions}
    per_item = {
        item_id: canonical_json(pred_map[item_id]) == canonical_json(gold_map[item_id])
        for item_id in sorted(gold_map)
    }
    summary = ScoreSummary(
        total=len(gold),
        correct=sum(per_item.values()),
        accuracy=sum(per_item.values()) / len(gold),
    )
    expected_report = {
        "schema_version": 2,
        "total": summary.total,
        "correct": summary.correct,
        "accuracy": summary.accuracy,
        "per_item": per_item,
    }
    if canonical_json(controller_report) != canonical_json(expected_report):
        raise ValueError("stored controller score report does not replay")
    if summary != attempt.score or per_item != dict(attempt.per_item):
        raise ValueError("replayed score does not match the solver attempt")
    return {
        "epoch_id": epoch_id,
        "attempt_id": attempt_id,
        "instance_digest": instance.instance_digest,
        "score": summary,
        "per_item": per_item,
        "debrief_digest": attempt.debrief_digest,
        "model_call_used": False,
        "verified": True,
    }

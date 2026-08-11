"""Referee Evaluator: 5-step sandbox preflight validation and candidate scoring."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from compat import Context
from referee.feedback_generator import generate_prompt_gradient
from sandbox.contracts import (
    bundle_leaks,
    generated_payload_digest,
    read_jsonl_strict,
    validate_answer_rows,
    validate_artifact_tree,
    validate_item_rows,
)
from sandbox.isolation import ScratchpadEnvironment

logger = logging.getLogger("bba.referee.evaluator")


def run_preflight_checks(benchmark_dir: str) -> Tuple[bool, Optional[str]]:
    """Executes the strict 5-step sandbox preflight validation."""
    b_path = Path(benchmark_dir)
    if not b_path.exists() or not b_path.is_dir():
        return False, f"Benchmark directory not found: {benchmark_dir}"

    # Step 1: File structure and artifact tree validation
    required_files = [
        "generator.py",
        "verifier.py",
        "scorer.py",
        "benchmark_spec.json",
        "validation_report.md",
        "gold_private_sample.jsonl",
        "negative_control_sample.jsonl",
    ]
    for rf in required_files:
        if not (b_path / rf).exists():
            return False, f"Missing required benchmark artifact: {rf}"

    solver_bundle = b_path / "solver_bundle"
    if not solver_bundle.exists() or not (solver_bundle / "solver_packet.md").exists() or not (solver_bundle / "items_private_sample.jsonl").exists():
        return False, "solver_bundle/ missing solver_packet.md or items_private_sample.jsonl"

    valid_tree, tree_err = validate_artifact_tree(str(b_path))
    if not valid_tree:
        return False, f"Artifact tree validation failed: {tree_err}"

    # Step 2: Seed 42 x 2 digest invariance
    with ScratchpadEnvironment() as sp1, ScratchpadEnvironment() as sp2:
        generator_script = str(b_path / "generator.py")
        ret1, _, err1 = sp1.run_python_code(generator_script, args=["--seed", "42", "--output_dir", sp1.path])
        ret2, _, err2 = sp2.run_python_code(generator_script, args=["--seed", "42", "--output_dir", sp2.path])
        if ret1 != 0 or ret2 != 0:
            return False, f"generator.py execution failed in sandbox: {err1 or err2}"

        d1 = generated_payload_digest(sp1.path)
        d2 = generated_payload_digest(sp2.path)
        if d1 != d2:
            return False, f"Seed 42 determinism violation: digest1 ({d1}) != digest2 ({d2})"

    # Step 3: 30/30 Gold self-verification
    gold_path = str(b_path / "gold_private_sample.jsonl")
    gold_rows = read_jsonl_strict(gold_path)
    valid_gold, gold_err = validate_answer_rows(gold_rows, expected_count=30)
    if not valid_gold:
        return False, f"Gold private sample schema error: {gold_err}"

    with ScratchpadEnvironment() as sp:
        verifier_script = str(b_path / "verifier.py")
        ret_gold, out_gold, err_gold = sp.run_python_code(
            verifier_script,
            args=["--predictions", gold_path, "--gold", gold_path],
        )
        if ret_gold != 0:
            return False, f"Gold self-verification failed (expected 30/30 exact match): {err_gold or out_gold}"

    # Step 4: 0/30 Negative control verification
    neg_path = str(b_path / "negative_control_sample.jsonl")
    neg_rows = read_jsonl_strict(neg_path)
    valid_neg, neg_err = validate_answer_rows(neg_rows, expected_count=30)
    if not valid_neg:
        return False, f"Negative control sample schema error: {neg_err}"

    with ScratchpadEnvironment() as sp:
        ret_neg, out_neg, err_neg = sp.run_python_code(
            verifier_script,
            args=["--predictions", neg_path, "--gold", gold_path],
        )
        # verifier should exit non-zero for negative control
        if ret_neg == 0:
            return False, "Negative control verification failed: corrupt predictions unexpectedly passed verifier!"
        try:
            neg_report = json.loads(out_neg)
            if neg_report.get("correct", -1) != 0:
                return False, f"Negative control returned non-zero correct matches ({neg_report.get('correct')})"
        except Exception:
            pass

    # Step 5: Anti-leakage audit
    leaks = bundle_leaks(str(solver_bundle), gold_rows)
    if leaks:
        return False, f"Anti-leakage audit failed: {leaks}"

    return True, None


async def sandbox_preflight_node(context: Context) -> str:
    """Preflight validation node for the ADK Workflow graph."""
    benchmark_dir = context.get_state("benchmark_dir")
    max_repairs = context.get_state("max_repairs", 3)
    repair_attempts = context.get_state("repair_attempts", 0)

    is_valid, error_msg = run_preflight_checks(benchmark_dir)

    if is_valid:
        context.route = "VALID_CANDIDATE"
        context.set_state("preflight_status", "PASSED")
        context.output = f"Preflight validation passed for {benchmark_dir}"
        logger.info(context.output)
        return context.output

    # Failed validation
    logger.warning(f"Preflight validation failed: {error_msg}")
    context.set_state("preflight_error", error_msg)

    if repair_attempts < max_repairs:
        context.route = "REPAIR_NEEDED"
        context.output = f"Preflight failed: {error_msg} -> Routing to repair_agent (attempt {repair_attempts+1}/{max_repairs})"
    else:
        context.route = "DISQUALIFIED"
        context.output = f"Preflight failed and max repairs exceeded ({repair_attempts}/{max_repairs}): {error_msg}"

    return context.output


async def referee_evaluator_node(context: Context) -> str:
    """Adjudicates solver predictions, assesses canonical equilibrium, and extracts prompt gradients."""
    benchmark_dir = context.get_state("benchmark_dir")
    predictions_path = context.get_state("predictions_path")
    gold_path = os.path.join(benchmark_dir, "gold_private_sample.jsonl")
    round_num = context.get_state("round", 1)
    max_rounds = context.get_state("max_rounds", 3)

    scorer_script = os.path.join(benchmark_dir, "scorer.py")

    with ScratchpadEnvironment() as sp:
        ret, out, err = sp.run_python_code(
            scorer_script,
            args=["--predictions", predictions_path, "--gold", gold_path],
        )

    try:
        score_report = json.loads(out)
    except Exception:
        # Fallback scoring directly
        pred_rows = read_jsonl_strict(predictions_path)
        gold_rows = read_jsonl_strict(gold_path)
        gold_map = {r["id"]: str(r["answer"]).strip() for r in gold_rows}
        correct = sum(1 for r in pred_rows if gold_map.get(r["id"]) == str(r["answer"]).strip())
        tot = len(gold_rows)
        score_report = {
            "total_items": tot,
            "correct_count": correct,
            "accuracy": (correct / tot) if tot > 0 else 0.0,
            "is_canonical_equilibrium": (10 <= correct <= 18),
        }


    correct_count = score_report.get("correct_count", 0)
    total_items = score_report.get("total_items", 30)
    is_canonical = score_report.get("is_canonical_equilibrium", False)

    context.set_state("evaluation_report", score_report)
    context.set_state("last_score", f"{correct_count}/{total_items}")

    # Generate prompt gradients
    spec_path = os.path.join(benchmark_dir, "benchmark_spec.json")
    spec = {}
    if os.path.exists(spec_path):
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
    gradient = generate_prompt_gradient(score_report, spec, round_num)
    context.set_state("prompt_gradient", gradient)

    if is_canonical:
        context.route = "CANONICAL_EQUILIBRIUM"
        context.set_state("coevolution_status", "EQUILIBRIUM_REACHED")
        context.output = f"Canonical Equilibrium achieved in Round {round_num}: solver score {correct_count}/{total_items} ({score_report.get('accuracy', 0)*100:.1f}%)"
    elif round_num < max_rounds:
        context.route = "EVOLVE_NEXT_ROUND"
        context.set_state("round", round_num + 1)
        context.output = f"Round {round_num} evaluation: {correct_count}/{total_items}. Evolving next round with prompt gradients."
    else:
        context.route = "CONCLUDED"
        context.set_state("coevolution_status", "MAX_ROUNDS_REACHED")
        context.output = f"Co-evolution concluded after {round_num} rounds. Final score: {correct_count}/{total_items}."

    logger.info(context.output)
    return context.output

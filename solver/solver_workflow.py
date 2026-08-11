"""ADK Sub-Workflow for bundle exploration and multi-item solving."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from compat import START, Context, Workflow
from sandbox.contracts import read_jsonl_strict, validate_answer_rows
from solver.item_solver_agent import item_solver_agent
from solver.tools import submit_prediction_row

logger = logging.getLogger("bba.solver.solver_workflow")


async def execute_solver_workflow(context: Context) -> str:
    """Executes the solver sub-workflow over all items in the candidate benchmark."""
    benchmark_dir = context.get_state("benchmark_dir")
    if not benchmark_dir or not os.path.exists(benchmark_dir):
        raise FileNotFoundError(f"Benchmark directory not found in state: {benchmark_dir}")

    solver_bundle_dir = os.path.join(benchmark_dir, "solver_bundle")
    items_file = os.path.join(solver_bundle_dir, "items_private_sample.jsonl")

    if not os.path.exists(items_file):
        raise FileNotFoundError(f"Items file not found: {items_file}")

    items = read_jsonl_strict(items_file)

    # Set up predictions output path
    predictions_dir = os.path.join(benchmark_dir, "solver_run")
    os.makedirs(predictions_dir, exist_ok=True)
    predictions_path = os.path.join(predictions_dir, "predictions.jsonl")

    # Clear previous predictions if any
    if os.path.exists(predictions_path):
        os.remove(predictions_path)

    context.set_state("solver_bundle_dir", solver_bundle_dir)
    context.set_state("predictions_path", predictions_path)

    # Process all items dynamically via run_node
    solved_count = 0
    for item in items:
        await context.run_node(item_solver_agent, node_input=item)
        solved_count += 1

    # Validate output predictions
    pred_rows = read_jsonl_strict(predictions_path)
    valid, err = validate_answer_rows(pred_rows, expected_count=len(items))
    if not valid:
        raise ValueError(f"Solver produced invalid predictions.jsonl: {err}")

    context.set_state("predictions_path", predictions_path)
    context.set_state("solved_count", solved_count)
    context.output = f"Solver successfully solved {solved_count} items into {predictions_path}"
    logger.info(context.output)
    return context.output


# ADK Sub-Workflow definition
solver_workflow = Workflow(
    name="solver_sub_workflow",
    nodes=[execute_solver_workflow],
    edges=[(START, execute_solver_workflow)],
)


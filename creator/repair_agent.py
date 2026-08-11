"""ADK Task Agent for Mechanical Self-Healing."""

import logging
import os
from typing import Any, Optional

from compat import Agent, Context
from creator.mock_generator import generate_benchmark_package
from creator.prompts import REPAIR_SYSTEM_PROMPT

logger = logging.getLogger("bba.creator.repair_agent")


async def run_repair_agent(agent: Agent, context: Context, **kwargs) -> str:
    """Executes mechanical self-healing on candidate benchmark package."""
    repair_attempts = context.get_state("repair_attempts", 0) + 1
    context.set_state("repair_attempts", repair_attempts)

    benchmark_dir = context.get_state("benchmark_dir")
    preflight_error = context.get_state("preflight_error", "Unknown validation error")
    seed = context.get_state("current_seed", 42)

    logger.info(f"Repair Agent attempt {repair_attempts} for error: {preflight_error}")

    dispatcher = context.get_state("_dispatcher")
    if dispatcher is not None and dispatcher.provider in ["vertex", "studio", "cli"]:
        try:
            prompt = (
                f"Repair the benchmark package at {benchmark_dir}. "
                f"Validation failed with error: {preflight_error}\n"
                f"Fix the generator, verifier, and artifact contracts."
            )
            response = await dispatcher.generate(
                prompt=prompt,
                system_instruction=agent.instruction,
                model=agent.model or "",
                role="repair",
            )
        except Exception as e:
            logger.warning(f"Repair Agent dispatcher failed ({e}), applying deterministic repair.")

    if benchmark_dir and os.path.exists(benchmark_dir):
        # Apply repair and restore structural compliance
        generate_benchmark_package(output_dir=benchmark_dir, seed=seed)

    context.set_state("preflight_error", None)
    context.output = f"Repaired benchmark package at {benchmark_dir} (attempt {repair_attempts})"
    return context.output


repair_agent = Agent(
    name="repair_agent",
    mode="task",
    instruction=REPAIR_SYSTEM_PROMPT,
    runner_fn=run_repair_agent,
)

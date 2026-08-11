"""ADK Task Agent for Benchmark Synthesis."""

import logging
import os
from pathlib import Path
from typing import Any, Optional

from compat import Agent, Context
from creator.mock_generator import generate_benchmark_package
from creator.prompts import CREATOR_SYSTEM_PROMPT

logger = logging.getLogger("bba.creator.creator_agent")


async def run_creator_agent(agent: Agent, context: Context, **kwargs) -> str:
    """Executes benchmark package synthesis."""
    round_num = context.get_state("round", 1)
    base_seed = context.get_state("base_seed", 42)
    seed = base_seed + (round_num - 1) * 7
    base_scratch = context.get_state("scratch_dir", "/tmp/bba_workspace")
    benchmark_dir = os.path.join(base_scratch, f"round_{round_num}_candidate")

    # Reset repair_attempts for each new round
    context.set_state("repair_attempts", 0)
    os.makedirs(benchmark_dir, exist_ok=True)

    dispatcher = context.get_state("_dispatcher")
    if dispatcher is not None and dispatcher.provider in ["vertex", "studio", "cli"]:
        try:
            prompt = (
                f"Synthesize a complete benchmark package for Financial / Expense Forensics (BBA-FEF) "
                f"at seed {seed}."
            )
            response = await dispatcher.generate(
                prompt=prompt,
                system_instruction=agent.instruction,
                model=agent.model or "",
                role="creator",
            )
            logger.info(f"Creator Agent dispatched to {dispatcher.provider}")
        except Exception as e:
            logger.warning(f"Dispatcher generation failed ({e}), falling back to deterministic synthesis.")

    # Synthesize benchmark package
    generate_benchmark_package(output_dir=benchmark_dir, seed=seed)

    context.set_state("benchmark_dir", benchmark_dir)
    context.set_state("current_seed", seed)
    context.set_state("round", round_num)
    context.output = f"Successfully generated candidate benchmark at {benchmark_dir} (seed={seed})"
    return context.output



creator_agent = Agent(
    name="creator_agent",
    mode="task",
    instruction=CREATOR_SYSTEM_PROMPT,
    runner_fn=run_creator_agent,
)

"""Root ADK 2.0 Workflow entrypoint for BenchBenchAgent (BBA).

Coordinates the closed-loop adversarial co-evolution minimax game between
Creator Agent, Sandbox Preflight Validator, Repair Agent, Solver Sub-Workflow,
and Referee Evaluator.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from backend.dispatcher import ModelDispatcher
from compat import DEFAULT_ROUTE, START, Context, Runner, Workflow
from config import BBAConfig, load_config
from creator.creator_agent import creator_agent
from creator.repair_agent import repair_agent
from referee.evaluator import referee_evaluator_node, sandbox_preflight_node
from solver.solver_workflow import solver_workflow

logger = logging.getLogger("bba.agent")


def build_bba_adversarial_workflow(name: str = "bba_adversarial_coevolution") -> Workflow:
    """Compiles the complete ADK 2.0 directed graph for BBA co-evolution."""
    wf = Workflow(name=name)

    # Nodes
    wf.add_node(creator_agent, "creator_agent")
    wf.add_node(sandbox_preflight_node, "sandbox_preflight_node")
    wf.add_node(repair_agent, "repair_agent")
    wf.add_node(solver_workflow, "solver_workflow")
    wf.add_node(referee_evaluator_node, "referee_evaluator_node")

    # Edges
    # 1. START -> creator_agent
    wf.add_edge(START, creator_agent)

    # 2. creator_agent -> sandbox_preflight_node
    wf.add_edge(creator_agent, sandbox_preflight_node)

    # 3. sandbox_preflight_node -> routing
    wf.add_edge(sandbox_preflight_node, {
        "REPAIR_NEEDED": repair_agent,
        "VALID_CANDIDATE": solver_workflow,
        "DISQUALIFIED": None,
    })

    # 4. repair_agent -> sandbox_preflight_node
    wf.add_edge(repair_agent, sandbox_preflight_node)

    # 5. solver_workflow -> referee_evaluator_node
    wf.add_edge(solver_workflow, referee_evaluator_node)

    # 6. referee_evaluator_node -> routing
    wf.add_edge(referee_evaluator_node, {
        "EVOLVE_NEXT_ROUND": creator_agent,
        "CANONICAL_EQUILIBRIUM": None,
        "CONCLUDED": None,
    })

    return wf


# Singleton root workflow instance
bba_adversarial_workflow = build_bba_adversarial_workflow()


async def run_bba_adversarial_loop(
    config: Optional[BBAConfig] = None,
    initial_state: Optional[Dict[str, Any]] = None,
    session_id: str = "bba_session_001",
) -> Context:
    """Runs the full BBA adversarial co-evolution minimax loop asynchronously."""
    cfg = config or load_config()
    dispatcher = ModelDispatcher(config=cfg)

    state = {
        "round": 1,
        "max_rounds": cfg.max_rounds,
        "scratch_dir": cfg.scratch_dir,
        "domain": cfg.domain,
        "_dispatcher": dispatcher,
        "repair_attempts": 0,
        "max_repairs": 3,
    }
    if initial_state:
        state.update(initial_state)

    runner = Runner(bba_adversarial_workflow)
    ctx = await runner.run(session_id=session_id, initial_state=state)
    return ctx


def run_bba_sync(
    config: Optional[BBAConfig] = None,
    initial_state: Optional[Dict[str, Any]] = None,
    session_id: str = "bba_session_001",
) -> Context:
    """Synchronous execution wrapper for BBA adversarial co-evolution."""
    return asyncio.run(run_bba_adversarial_loop(config=config, initial_state=initial_state, session_id=session_id))

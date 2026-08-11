"""Unit tests for the complete BBA Adversarial Co-Evolution Loop."""

import os
import shutil
import tempfile
import unittest

from agent import (
    build_bba_adversarial_workflow,
    run_bba_adversarial_loop,
)
from compat import Context
from config import BBAConfig
from creator.mock_generator import generate_benchmark_package
from referee.evaluator import referee_evaluator_node
from referee.feedback_generator import generate_prompt_gradient
from solver.solver_workflow import solver_workflow


class TestBBAAdversarialLoop(unittest.IsolatedAsyncioTestCase):
    """End-to-end integration tests for closed-loop adversarial co-evolution."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="bba_test_loop_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_workflow_graph_structure(self):
        wf = build_bba_adversarial_workflow()
        self.assertEqual(wf.name, "bba_adversarial_coevolution")
        self.assertIn("creator_agent", wf.nodes)
        self.assertIn("sandbox_preflight_node", wf.nodes)
        self.assertIn("repair_agent", wf.nodes)
        self.assertIn("solver_workflow", wf.nodes)
        self.assertIn("referee_evaluator_node", wf.nodes)

    def test_feedback_prompt_gradient_generator(self):
        # 1. Test Too Easy
        report_easy = {"total_items": 30, "correct_count": 25, "accuracy": 25 / 30}
        grad_easy = generate_prompt_gradient(report_easy, round_num=1)
        self.assertEqual(grad_easy["discriminative_status"], "TOO_EASY")
        self.assertTrue(len(grad_easy["recommendations"]) > 0)

        # 2. Test Canonical Equilibrium (15/30)
        report_ideal = {"total_items": 30, "correct_count": 15, "accuracy": 0.5}
        grad_ideal = generate_prompt_gradient(report_ideal, round_num=1)
        self.assertEqual(grad_ideal["discriminative_status"], "IDEAL")

        # 3. Test Too Hard (5/30)
        report_hard = {"total_items": 30, "correct_count": 5, "accuracy": 5 / 30}
        grad_hard = generate_prompt_gradient(report_hard, round_num=1)
        self.assertEqual(grad_hard["discriminative_status"], "TOO_HARD")

    async def test_referee_evaluator_node_adjudication(self):
        generate_benchmark_package(self.test_dir, seed=42)

        # Run solver sub-workflow with 50% pass rate (15/30 items -> canonical equilibrium)
        ctx = Context(state={
            "benchmark_dir": self.test_dir,
            "solver_pass_rate": 0.5,
            "round": 1,
            "max_rounds": 3,
        })
        await solver_workflow(ctx)

        # Run referee evaluator node
        await referee_evaluator_node(ctx)
        self.assertEqual(ctx.route, "CANONICAL_EQUILIBRIUM")
        self.assertEqual(ctx.get_state("coevolution_status"), "EQUILIBRIUM_REACHED")
        self.assertIn("Canonical Equilibrium achieved", ctx.output)

    async def test_end_to_end_adversarial_coevolution_loop(self):
        config = BBAConfig(
            provider="mock",
            scratch_dir=self.test_dir,
            max_rounds=2,
        )

        ctx = await run_bba_adversarial_loop(
            config=config,
            initial_state={"solver_pass_rate": 0.5},
            session_id="integration_test_session",
        )

        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.get_state("preflight_status"), "PASSED")
        self.assertEqual(ctx.get_state("coevolution_status"), "EQUILIBRIUM_REACHED")

        # Check candidate benchmark artifacts exist
        benchmark_dir = ctx.get_state("benchmark_dir")
        self.assertTrue(os.path.exists(benchmark_dir))
        self.assertTrue(os.path.exists(os.path.join(benchmark_dir, "benchmark_spec.json")))
        self.assertTrue(os.path.exists(os.path.join(benchmark_dir, "validation_report.md")))
        self.assertTrue(os.path.exists(os.path.join(benchmark_dir, "gold_private_sample.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(benchmark_dir, "solver_bundle", "items_private_sample.jsonl")))


if __name__ == "__main__":
    unittest.main()

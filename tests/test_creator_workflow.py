"""Unit tests for Creator agent, preflight validator, Seed 42 invariance, and repair loop."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from compat import Context
from creator.creator_agent import creator_agent
from creator.mock_generator import generate_benchmark_package
from creator.repair_agent import repair_agent
from referee.evaluator import run_preflight_checks, sandbox_preflight_node
from sandbox.contracts import (
    bundle_leaks,
    generated_payload_digest,
    read_jsonl_strict,
    tree_digest,
    validate_answer_rows,
    validate_artifact_tree,
    validate_item_rows,
)
from sandbox.isolation import ScratchpadEnvironment


class TestCreatorWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests for benchmark synthesis, preflight validation, determinism, and repair."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="bba_test_creator_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_artifact_tree_validation(self):
        # Non-existent dir
        valid, err = validate_artifact_tree("/non/existent/path/for/bba/test")
        self.assertFalse(valid)
        self.assertIn("Directory does not exist", err)

        # Valid dir
        generate_benchmark_package(self.test_dir, seed=42)
        valid, err = validate_artifact_tree(self.test_dir)
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_seed_42_digest_invariance(self):
        dir1 = os.path.join(self.test_dir, "run1")
        dir2 = os.path.join(self.test_dir, "run2")

        generate_benchmark_package(dir1, seed=42)
        generate_benchmark_package(dir2, seed=42)

        d1 = generated_payload_digest(dir1)
        d2 = generated_payload_digest(dir2)

        self.assertEqual(d1, d2)
        self.assertTrue(len(d1) == 64)

    def test_strict_jsonl_and_row_validators(self):
        generate_benchmark_package(self.test_dir, seed=42)
        gold_path = os.path.join(self.test_dir, "gold_private_sample.jsonl")
        items_path = os.path.join(self.test_dir, "solver_bundle", "items_private_sample.jsonl")

        gold_rows = read_jsonl_strict(gold_path)
        valid_gold, err_gold = validate_answer_rows(gold_rows, expected_count=30)
        self.assertTrue(valid_gold)
        self.assertIsNone(err_gold)

        item_rows = read_jsonl_strict(items_path)
        valid_items, err_items = validate_item_rows(item_rows, expected_count=30)
        self.assertTrue(valid_items)
        self.assertIsNone(err_items)

        # Test corrupt answer row rejection
        bad_rows = [{"id": "fef_0001", "answer": "not_an_integer"}]
        valid_bad, err_bad = validate_answer_rows(bad_rows, expected_count=1)
        self.assertFalse(valid_bad)
        self.assertIn("not a valid integer USD cent value", err_bad)

        # Test duplicate item ID rejection
        dup_rows = [{"id": "fef_0001", "answer": "100"}, {"id": "fef_0001", "answer": "200"}]
        valid_dup, err_dup = validate_answer_rows(dup_rows, expected_count=2)
        self.assertFalse(valid_dup)
        self.assertIn("Duplicate item id", err_dup)

    def test_bundle_anti_leakage_scan(self):
        generate_benchmark_package(self.test_dir, seed=42)
        solver_bundle = os.path.join(self.test_dir, "solver_bundle")
        gold_rows = read_jsonl_strict(os.path.join(self.test_dir, "gold_private_sample.jsonl"))

        # Clean bundle should have 0 leaks
        leaks = bundle_leaks(solver_bundle, gold_rows)
        self.assertEqual(leaks, [])

        # Intentionally plant a forbidden file
        bad_file = os.path.join(solver_bundle, "gold_private_sample.jsonl")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("leaked ground truth")

        dirty_leaks = bundle_leaks(solver_bundle, gold_rows)
        self.assertTrue(len(dirty_leaks) > 0)

    def test_preflight_checks_pass_on_clean_package(self):
        generate_benchmark_package(self.test_dir, seed=42)
        valid, err = run_preflight_checks(self.test_dir)
        self.assertTrue(valid, f"Preflight checks failed: {err}")
        self.assertIsNone(err)

    async def test_creator_agent_node(self):
        ctx = Context(state={"scratch_dir": self.test_dir, "round": 1, "seed": 42})
        out = await creator_agent(ctx)
        self.assertIn("Successfully generated candidate benchmark", out)
        benchmark_dir = ctx.get_state("benchmark_dir")
        self.assertTrue(os.path.exists(benchmark_dir))
        self.assertTrue(os.path.exists(os.path.join(benchmark_dir, "benchmark_spec.json")))

    async def test_repair_agent_healing_flow(self):
        generate_benchmark_package(self.test_dir, seed=42)
        # Intentionally break generator.py
        gen_file = os.path.join(self.test_dir, "generator.py")
        with open(gen_file, "w", encoding="utf-8") as f:
            f.write("syntax error def main() :::: corrupt")

        ctx = Context(state={
            "benchmark_dir": self.test_dir,
            "current_seed": 42,
            "preflight_error": "Syntax error in generator.py",
            "repair_attempts": 0,
        })

        # Run preflight node (should detect failure and route to REPAIR_NEEDED)
        await sandbox_preflight_node(ctx)
        self.assertEqual(ctx.route, "REPAIR_NEEDED")

        # Run repair agent
        await repair_agent(ctx)
        self.assertEqual(ctx.get_state("repair_attempts"), 1)

        # Run preflight again (should pass now after repair)
        await sandbox_preflight_node(ctx)
        self.assertEqual(ctx.route, "VALID_CANDIDATE")


if __name__ == "__main__":
    unittest.main()

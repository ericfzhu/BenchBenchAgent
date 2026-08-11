"""Unit tests for Solver REPL tools, Decimal arithmetic, and item solver sub-workflow."""

import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from compat import Context
from creator.mock_generator import generate_benchmark_package
from sandbox.contracts import read_jsonl_strict, validate_answer_rows
from solver.item_solver_agent import _solve_item_deterministically, item_solver_agent
from solver.solver_workflow import solver_workflow
from solver.tools import (
    list_solver_assets,
    python_repl_tool,
    read_solver_asset,
    submit_prediction_row,
)


class TestSolverWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests for solver tool execution, path security, and solver sub-workflow."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="bba_test_solver_")
        generate_benchmark_package(self.test_dir, seed=42)
        self.bundle_dir = os.path.join(self.test_dir, "solver_bundle")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_python_repl_tool(self):
        # 1. Decimal math expression
        res1 = python_repl_tool("str(Decimal('45.00') + Decimal('10.35'))")
        self.assertEqual(res1, "55.35")

        # 2. Multi-line code execution with Decimal quantize
        code2 = """
food = Decimal('50.00')
tip = min((food * Decimal('0.20')).quantize(Decimal('0.01')), Decimal('12.00'))
total = (food + tip).quantize(Decimal('0.01'))
print(total)
"""
        res2 = python_repl_tool(code2)
        self.assertEqual(res2, "60.00")

        # 3. Error handling
        res3 = python_repl_tool("1 / 0")
        self.assertIn("ZeroDivisionError", res3)

    def test_safe_asset_reader_and_traversal_defense(self):
        # 1. Valid read
        rates_txt = read_solver_asset(self.bundle_dir, "assets/common/exchange_rates.csv")
        self.assertIn("rate_to_usd", rates_txt)
        self.assertIn("EUR", rates_txt)

        # 2. Case receipts read
        rec_txt = read_solver_asset(self.bundle_dir, "assets/cases/case_0001/receipts.txt")
        self.assertIn("EXPENSE RECEIPTS FOR CASE_0001", rec_txt)

        # 3. Path traversal defense
        with self.assertRaises(ValueError) as cm:
            read_solver_asset(self.bundle_dir, "../../gold_private_sample.jsonl")
        self.assertIn("path traversal", str(cm.exception).lower())

        # 4. Non-existent file
        with self.assertRaises(FileNotFoundError):
            read_solver_asset(self.bundle_dir, "assets/cases/case_9999/non_existent.txt")

    def test_list_solver_assets(self):
        assets = list_solver_assets(self.bundle_dir)
        self.assertTrue(len(assets) > 0)
        self.assertIn("assets/common/exchange_rates.csv", assets)
        self.assertIn("solver_packet.md", assets)

    def test_submit_prediction_row(self):
        pred_path = os.path.join(self.test_dir, "test_predictions.jsonl")

        # Valid integer string
        res1 = submit_prediction_row("fef_0001", "15992", pred_path)
        self.assertEqual(res1["status"], "SUCCESS")
        self.assertEqual(res1["answer"], "15992")

        # Valid Decimal
        res2 = submit_prediction_row("fef_0002", Decimal("4500.00"), pred_path)
        self.assertEqual(res2["answer"], "4500")

        # Read back
        rows = read_jsonl_strict(pred_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "fef_0001")
        self.assertEqual(rows[0]["answer"], "15992")

        # Invalid answer
        with self.assertRaises(ValueError):
            submit_prediction_row("fef_0003", "invalid_number", pred_path)

    def test_deterministic_solver_arithmetic(self):
        # Case 1: Domestic dinner
        ans1 = _solve_item_deterministically(self.bundle_dir, "fef_0001", "case_0001")
        self.assertEqual(ans1, "4535")

        # Case 21: Voided receipt -> $0.00
        ans21 = _solve_item_deterministically(self.bundle_dir, "fef_0021", "case_0021")
        self.assertEqual(ans21, "0")

        # Case 24: Commute under 15 miles -> $0.00
        ans24 = _solve_item_deterministically(self.bundle_dir, "fef_0024", "case_0024")
        self.assertEqual(ans24, "0")

    def test_all_30_items_deterministic_solvability(self):
        """Verifies that all 30 generated items are deterministically solvable and match gold."""
        gold_path = os.path.join(self.test_dir, "gold_private_sample.jsonl")
        gold_rows = read_jsonl_strict(gold_path)
        gold_map = {r["id"]: str(r["answer"]).strip() for r in gold_rows}

        for idx in range(1, 31):
            item_id = f"fef_{idx:04d}"
            case_id = f"case_{idx:04d}"
            solved_ans = _solve_item_deterministically(self.bundle_dir, item_id, case_id, fail_simulated=False)
            expected_gold = gold_map[item_id]
            self.assertEqual(
                solved_ans,
                expected_gold,
                f"Item {item_id} ({case_id}) mismatch: solved {solved_ans} != expected {expected_gold}",
            )

    async def test_solver_sub_workflow(self):
        ctx = Context(state={
            "benchmark_dir": self.test_dir,
            "solver_pass_rate": 0.5,
        })

        out = await solver_workflow(ctx)
        self.assertIn("Solver successfully solved 30 items", out)

        pred_path = ctx.get_state("predictions_path")
        self.assertTrue(os.path.exists(pred_path))

        pred_rows = read_jsonl_strict(pred_path)
        valid, err = validate_answer_rows(pred_rows, expected_count=30)
        self.assertTrue(valid, f"Generated predictions invalid: {err}")


if __name__ == "__main__":
    unittest.main()


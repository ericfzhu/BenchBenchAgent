"""Evaluator identity coverage tests."""

from __future__ import annotations

import unittest

from bba.evaluator_identity import BOUND_DISTRIBUTIONS, BOUND_MODULES


class TestEvaluatorIdentityCoverage(unittest.TestCase):
    def test_gcp_routing_source_and_runtime_are_digest_bound(self):
        self.assertIn("gcp.py", BOUND_MODULES)
        self.assertIn("google-auth", BOUND_DISTRIBUTIONS)
        self.assertIn("google-cloud-aiplatform", BOUND_DISTRIBUTIONS)
        self.assertIn("pydantic", BOUND_DISTRIBUTIONS)

    def test_facade_implementation_modules_are_digest_bound(self):
        for name in ("_audit_runner.py", "_evidence.py", "_tournament.py"):
            self.assertIn(name, BOUND_MODULES)


if __name__ == "__main__":
    unittest.main()

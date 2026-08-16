"""Operator portal quota readiness tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bba._operator_portal import OperatorConsole as PortalOperatorConsole
from bba.operator import OperatorConsole


class TestQuotaPortal(unittest.TestCase):
    def test_quota_diagnostic_is_exposed(self):
        self.assertEqual(
            OperatorConsole.DIAGNOSTIC_ACTIONS["quotas"],
            "Inspect effective model quotas",
        )

    def test_quota_failure_blocks_workspace_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            console = object.__new__(OperatorConsole)
            console.evidence = type("Evidence", (), {"root": Path(temporary)})()
            base = {
                "ready": True,
                "checks": [{
                    "id": "sandbox",
                    "label": "Generated-code sandbox",
                    "status": "passed",
                    "detail": "linux-bubblewrap",
                    "required": True,
                }],
                "catalog_version": "test",
                "model_count": 1,
                "python": "3.12",
                "google_adk": "2.6.3",
                "evidence_root": temporary,
            }
            with patch.object(PortalOperatorConsole, "readiness", return_value=base):
                with patch.object(
                    OperatorConsole,
                    "_quota_status",
                    side_effect=RuntimeError("quota permission missing"),
                ):
                    value = console.readiness()
            self.assertFalse(value["ready"])
            quota = next(item for item in value["checks"] if item["id"] == "quota")
            self.assertEqual(quota["status"], "failed")
            self.assertIn("quota permission", quota["detail"])


if __name__ == "__main__":
    unittest.main()

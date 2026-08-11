"""Local epoch CLI and saved-state tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bba.cli import main


class TestLocalEpochCli(unittest.TestCase):
    def test_create_and_status_use_only_the_local_evidence_root(self):
        epoch_id = "local-cli-test"
        with tempfile.TemporaryDirectory(prefix="bba-cli-state-") as temporary:
            root = Path(temporary) / "local-evidence"
            output = io.StringIO()
            with patch("bba.cli.discover_gcp_project", return_value="bba-test-project"):
                with redirect_stdout(output):
                    result = main([
                        "epoch",
                        "create",
                        "--epoch-id",
                        epoch_id,
                        "--evidence-root",
                        str(root),
                    ])
            self.assertEqual(result, 0)
            created = json.loads(output.getvalue())
            self.assertEqual(created["phase"], "created")
            self.assertTrue((root / "bba-state.sqlite3").is_file())
            manifest_path = root / "epochs" / epoch_id / "manifest.json"
            private_path = root / "epochs" / epoch_id / "private" / "holdout-plan.json"
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(private_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["catalog_version"], "gcp-serverless-2026-08-12")
            self.assertEqual(manifest["gcp_location"], "global")
            self.assertEqual(len(manifest["cohort"]), 12)

            output = io.StringIO()
            with redirect_stdout(output):
                result = main([
                    "epoch",
                    "status",
                    "--epoch-id",
                    epoch_id,
                    "--evidence-root",
                    str(root),
                ])
            self.assertEqual(result, 0)
            status = json.loads(output.getvalue())
            self.assertEqual(status["epoch_id"], epoch_id)
            self.assertEqual(status["work_counts"], {})


if __name__ == "__main__":
    unittest.main()

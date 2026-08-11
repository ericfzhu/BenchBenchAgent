"""Local epoch CLI and saved-state tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from bba.cli import main


class TestLocalEpochCli(unittest.TestCase):
    def test_create_and_status_use_only_the_local_evidence_root(self):
        source = Path(__file__).parents[1] / "examples" / "serverless-pilot-manifest.json"
        manifest = json.loads(source.read_text(encoding="utf-8"))
        manifest["hidden_commitments"] = {
            "hidden_solver_panel": "a" * 64,
            "hidden_seeds": "b" * 64,
            "audit_policy": "c" * 64,
        }
        with tempfile.TemporaryDirectory(prefix="bba-cli-state-") as temporary:
            root = Path(temporary) / "local-evidence"
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main([
                    "epoch",
                    "create",
                    "--manifest",
                    str(manifest_path),
                    "--evidence-root",
                    str(root),
                ])
            self.assertEqual(result, 0)
            created = json.loads(output.getvalue())
            self.assertEqual(created["phase"], "created")
            self.assertTrue((root / "bba-state.sqlite3").is_file())
            self.assertTrue(
                (root / "epochs" / manifest["epoch_id"] / "manifest.json").is_file()
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = main([
                    "epoch",
                    "status",
                    "--epoch-id",
                    manifest["epoch_id"],
                    "--evidence-root",
                    str(root),
                ])
            self.assertEqual(result, 0)
            status = json.loads(output.getvalue())
            self.assertEqual(status["epoch_id"], manifest["epoch_id"])
            self.assertEqual(status["work_counts"], {})


if __name__ == "__main__":
    unittest.main()

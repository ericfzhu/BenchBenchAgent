"""Local epoch CLI and saved-state tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bba.cli import main
from bba.protocol import digest_json
from bba.state import LocalStateStore


class TestLocalEpochCli(unittest.TestCase):
    def test_inference_reservations_are_idempotent_and_bounded(self):
        from tests.test_end_state_protocol import manifest

        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            value = manifest("reservation-test")
            state.register_epoch(value)
            limits = {"calls": 2, "input_tokens": 10, "output_tokens": 10}
            state.reserve_inference(value.epoch_id, "one", 1, 5, 5, limits)
            state.reserve_inference(value.epoch_id, "one", 1, 5, 5, limits)
            state.reconcile_inference(value.epoch_id, "one", 1, 2, 3)
            self.assertEqual(
                state.inference_usage(value.epoch_id),
                {"calls": 1, "input_tokens": 2, "output_tokens": 3},
            )
            with self.assertRaises(RuntimeError):
                state.reserve_inference(value.epoch_id, "two", 2, 1, 1, limits)

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
            private = json.loads(private_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["catalog_version"], "gcp-serverless-2026-08-12")
            self.assertEqual(manifest["gcp_location"], "global")
            self.assertEqual(len(manifest["cohort"]), 12)
            self.assertEqual(
                manifest["hidden_commitments"],
                {name: digest_json(value) for name, value in private.items()},
            )

            repeated = io.StringIO()
            with patch("bba.cli.discover_gcp_project", return_value="bba-test-project"):
                with redirect_stdout(repeated):
                    result = main([
                        "epoch",
                        "create",
                        "--epoch-id",
                        epoch_id,
                        "--evidence-root",
                        str(root),
                    ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(repeated.getvalue())["phase"], "created")

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

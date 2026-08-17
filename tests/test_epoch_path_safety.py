"""Epoch evidence paths cannot escape their direct evidence-root child."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from bba.cli import build_parser
from bba.evidence import EvidenceStore


class TestEpochPathSafety(unittest.TestCase):
    def test_special_and_traversal_components_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore(Path(temporary) / "evidence")
            for epoch_id in ("", ".", "..", "../outside", "child/inside"):
                with self.subTest(epoch_id=epoch_id):
                    with self.assertRaises(ValueError):
                        store.epoch_root(epoch_id)

    def test_valid_epoch_is_one_direct_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore(Path(temporary) / "evidence")
            root = store.epoch_root("epoch-2026.08")
            self.assertEqual(root.parent, (store.root / "epochs").resolve())
            self.assertEqual(root.name, "epoch-2026.08")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = EvidenceStore(base / "evidence")
            epochs = store.root / "epochs"
            epochs.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            try:
                (epochs / "linked-epoch").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            with self.assertRaises(ValueError):
                store.epoch_root("linked-epoch")

    def test_destructive_epoch_delete_command_is_not_exposed(self):
        parser = build_parser()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "epoch",
                        "delete",
                        "--epoch-id",
                        "epoch-one",
                    ]
                )
        self.assertIn("invalid choice", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

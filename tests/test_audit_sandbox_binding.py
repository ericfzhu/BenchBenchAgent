"""Audit sandbox conformance tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bba.audit_runner import SealedAuditRunner


class TestAuditSandboxBinding(unittest.TestCase):
    def _controller(self, backend="linux-bubblewrap"):
        return SimpleNamespace(
            manifest=SimpleNamespace(
                sandbox=SimpleNamespace(backend=backend)
            ),
            evidence=SimpleNamespace(),
        )

    def test_unavailable_audit_sandbox_is_rejected(self):
        validator = SimpleNamespace(
            sandbox=SimpleNamespace(
                available=False,
                backend="unavailable",
                unavailable_reason="Bubblewrap namespace probe failed",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "Bubblewrap"):
            SealedAuditRunner(self._controller(), validator, {})

    def test_backend_must_match_frozen_epoch(self):
        validator = SimpleNamespace(
            sandbox=SimpleNamespace(
                available=True,
                backend="macos-seatbelt",
                unavailable_reason="",
            )
        )
        with self.assertRaisesRegex(ValueError, "frozen epoch manifest"):
            SealedAuditRunner(self._controller(), validator, {})


if __name__ == "__main__":
    unittest.main()

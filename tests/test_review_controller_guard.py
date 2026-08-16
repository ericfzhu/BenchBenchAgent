"""Controller-level review freeze tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bba._tournament import TournamentController as CoreTournamentController
from bba.tournament import TournamentController


class TestReviewControllerGuard(unittest.TestCase):
    def _closed_controller(self):
        controller = object.__new__(TournamentController)
        controller.manifest = SimpleNamespace(epoch_id="closed-review-test")
        controller.evidence = SimpleNamespace(
            review_window_closed=lambda _epoch_id: True
        )
        return controller

    def test_late_certificate_is_rejected_before_core_mutation(self):
        controller = self._closed_controller()
        with patch.object(
            CoreTournamentController,
            "record_solvability_certificate",
        ) as core:
            with self.assertRaisesRegex(RuntimeError, "review window closed"):
                controller.record_solvability_certificate(object())
        core.assert_not_called()

    def test_late_review_is_rejected_before_trust_registration(self):
        controller = self._closed_controller()
        with patch.object(
            CoreTournamentController,
            "record_human_review",
        ) as core:
            with self.assertRaisesRegex(RuntimeError, "review window closed"):
                controller.record_human_review(object())
        core.assert_not_called()


if __name__ == "__main__":
    unittest.main()

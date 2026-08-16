"""Public-close recovery tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bba._tournament import TournamentController as CoreTournamentController
from bba.tournament import TournamentController


class TestPublicCloseRecovery(unittest.TestCase):
    def test_canonical_promotions_are_replayed_after_core_recovery(self):
        controller = object.__new__(TournamentController)
        controller.evidence = Mock()
        promotion = SimpleNamespace(design_digest="design-one")
        controller.promotions = {promotion.design_digest: promotion}

        registry = Mock()
        with patch.object(
            CoreTournamentController,
            "close_public_epoch",
            return_value={"epoch_id": "recovered"},
        ), patch("bba.tournament.PromotionRegistry", return_value=registry):
            record = controller.close_public_epoch()

        self.assertEqual(record, {"epoch_id": "recovered"})
        registry.append.assert_called_once_with(promotion)


if __name__ == "__main__":
    unittest.main()

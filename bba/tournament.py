"""Public tournament API with crash-safe public-close recovery."""

from __future__ import annotations

from bba._tournament import *  # noqa: F401,F403
from bba._tournament import TournamentController as _TournamentController
from bba.registry import PromotionRegistry


class TournamentController(_TournamentController):
    """Controller facade that completes canonical publication after recovery."""

    def _publish_canonical_promotions(self) -> None:
        registry = PromotionRegistry(self.evidence)
        for promotion in sorted(
            self.promotions.values(), key=lambda item: item.design_digest
        ):
            registry.append(promotion)

    def close_public_epoch(self):
        # The core operation publishes immutable evaluation evidence before it
        # appends canonical promotions. If the process stops between those
        # steps, the next call takes the core recovery path. Always replay the
        # append-only publication after that path returns; registry.append is
        # idempotent for an identical signed promotion.
        record = super().close_public_epoch()
        self._publish_canonical_promotions()
        return record

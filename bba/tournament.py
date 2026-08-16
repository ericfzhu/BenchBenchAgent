"""Public tournament API with review and publication recovery guards."""

from __future__ import annotations

from bba._tournament import *  # noqa: F401,F403
from bba._tournament import TournamentController as _TournamentController
from bba.registry import PromotionRegistry


class TournamentController(_TournamentController):
    """Controller facade for immutable review and publication boundaries."""

    def _require_review_window_open(self) -> None:
        checker = getattr(self.evidence, "review_window_closed", None)
        if checker is not None and checker(self.manifest.epoch_id):
            raise RuntimeError(
                "the review window closed when the public audit population was frozen"
            )

    def record_solvability_certificate(self, *args, **kwargs):
        self._require_review_window_open()
        return super().record_solvability_certificate(*args, **kwargs)

    def record_human_review(self, *args, **kwargs):
        # Check before the core implementation can add a reviewer trust key or
        # publish any other review-adjacent registry record.
        self._require_review_window_open()
        return super().record_human_review(*args, **kwargs)

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

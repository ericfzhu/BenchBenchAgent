"""Public tournament API with consistent session budgets and recovery guards."""

from __future__ import annotations

from typing import Any

from bba._tournament import *  # noqa: F401,F403
from bba._tournament import TournamentController as _TournamentController
from bba.registry import PromotionRegistry
from bba.session_budget import agent_session_budget


class _SessionBudgetState:
    """Translate logical ADK reservations into the frozen session envelope."""

    def __init__(self, state: Any, resource_budget: Any) -> None:
        self._state = state
        self._resource_budget = resource_budget
        self._session = agent_session_budget(resource_budget)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)

    def reserve_inference(
        self,
        epoch_id: str,
        reservation_id: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        limits,
    ) -> None:
        """Reserve the same cumulative limits enforced by the ADK plugin."""

        if (
            int(calls) == self._session.max_llm_calls
            and int(input_tokens) == int(self._resource_budget.max_tokens)
            and int(output_tokens) == int(self._resource_budget.max_tokens)
        ):
            input_tokens = self._session.max_session_input_tokens
            output_tokens = self._session.max_session_output_tokens
        self._state.reserve_inference(
            epoch_id,
            reservation_id,
            calls,
            input_tokens,
            output_tokens,
            limits,
        )


class TournamentController(_TournamentController):
    """Controller facade for token, review, and publication boundaries."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(self.state, _SessionBudgetState):
            self.state = _SessionBudgetState(self.state, self.manifest.budget)

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

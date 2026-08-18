"""Public tournament API with consistent session budgets and recovery guards."""

from __future__ import annotations

from typing import Any

from bba._tournament import *  # noqa: F401,F403
from bba._tournament import TournamentController as _TournamentController
from bba.pricing import model_cost_context
from bba.registry import PromotionRegistry
from bba.session_budget import agent_session_budget


class _SessionBudgetState:
    """Translate logical ADK reservations into the frozen session envelope."""

    def __init__(
        self,
        state: Any,
        resource_budget: Any,
        cohort,
        *,
        cost_exempt: bool = False,
    ) -> None:
        self._state = state
        self._resource_budget = resource_budget
        self._session = agent_session_budget(resource_budget)
        self._cohort = tuple(cohort)
        self._cost_exempt = bool(cost_exempt)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)

    def _model_for_reservation(self, reservation_id: str) -> str:
        """Recover the exact price key from the frozen identity in the ID."""

        value = str(reservation_id)
        for identity in self._cohort:
            if identity.artifact_id in value:
                return identity.model

        # Hidden-audit identities change only the scaffold segment, so match
        # their stable publisher/model prefix against the immutable attempt ID.
        for identity in self._cohort:
            prefix = f"gcp__{identity.publisher}__{identity.model}__"
            if prefix in value:
                return identity.model
        raise RuntimeError(
            "could not attribute inference reservation to a frozen model route: "
            + value
        )

    def reserve_inference(
        self,
        epoch_id: str,
        reservation_id: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        limits,
        *,
        model: str | None = None,
    ) -> None:
        """Reserve the same cumulative limits enforced by the ADK plugin."""

        if (
            int(calls) == self._session.max_llm_calls
            and int(input_tokens) == int(self._resource_budget.max_tokens)
            and int(output_tokens) == int(self._resource_budget.max_tokens)
        ):
            input_tokens = self._session.max_session_input_tokens
            output_tokens = self._session.max_session_output_tokens
        selected_model = model or self._model_for_reservation(reservation_id)
        with model_cost_context(
            selected_model,
            cost_exempt=self._cost_exempt,
        ):
            self._state.reserve_inference(
                epoch_id,
                reservation_id,
                calls,
                input_tokens,
                output_tokens,
                limits,
            )

    def reconcile_inference(
        self,
        epoch_id: str,
        reservation_id: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Reconcile using the same exact route that priced the reservation."""

        selected_model = self._model_for_reservation(reservation_id)
        if hasattr(self._state, "_connect") and hasattr(self._state, "_reservation_storage_id"):
            with self._state._connect() as connection:
                storage_id = self._state._reservation_storage_id(
                    connection, epoch_id, reservation_id, legacy_fallback=True
                )
                row = connection.execute(
                    "SELECT reserved_input_tokens, reserved_output_tokens "
                    "FROM inference_reservations WHERE epoch_id = ? AND reservation_id = ?",
                    (epoch_id, storage_id),
                ).fetchone()
                if row is not None:
                    if row["reserved_input_tokens"] is not None:
                        input_tokens = min(input_tokens, int(row["reserved_input_tokens"]))
                    if row["reserved_output_tokens"] is not None:
                        output_tokens = min(output_tokens, int(row["reserved_output_tokens"]))

        with model_cost_context(
            selected_model,
            cost_exempt=self._cost_exempt,
        ):
            self._state.reconcile_inference(
                epoch_id,
                reservation_id,
                calls,
                input_tokens,
                output_tokens,
            )


class TournamentController(_TournamentController):
    """Controller facade for token, review, and publication boundaries."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(self.state, _SessionBudgetState):
            self.state = _SessionBudgetState(
                self.state,
                self.manifest.budget,
                self.manifest.cohort,
                cost_exempt=(
                    self.manifest.sandbox.backend == "trusted-fixture-only"
                ),
            )

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

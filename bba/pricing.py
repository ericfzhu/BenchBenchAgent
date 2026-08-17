"""Versioned model prices and realistic uncached epoch cost estimates."""

from __future__ import annotations

import contextvars
import json
import math
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping

from bba.protocol import ExperimentManifest, digest_json
from bba.session_budget import agent_session_budget


@dataclass(frozen=True)
class CostAttribution:
    """The exact route applied to one state-ledger operation."""

    model: str
    cost_exempt: bool = False


_COST_ATTRIBUTION: contextvars.ContextVar[CostAttribution | None] = (
    contextvars.ContextVar("bba_cost_attribution", default=None)
)


@contextmanager
def model_cost_context(
    model: str,
    *,
    cost_exempt: bool = False,
) -> Iterator[CostAttribution]:
    """Bind one reservation/reconciliation to its frozen model price key."""

    model_name = str(model).strip()
    if not model_name:
        raise ValueError("model price key cannot be blank")
    value = CostAttribution(model_name, bool(cost_exempt))
    token = _COST_ATTRIBUTION.set(value)
    try:
        yield value
    finally:
        _COST_ATTRIBUTION.reset(token)


def current_cost_attribution() -> CostAttribution | None:
    return _COST_ATTRIBUTION.get()


_PROFILE_FIELDS = (
    "creator_input_tokens",
    "creator_output_tokens",
    "solver_input_tokens",
    "solver_output_tokens",
    "solver_retry_rate",
)


class PriceCatalog:
    """Load frozen token rates and calculate provider and budgeted costs."""

    def __init__(self, path: Path | None = None):
        self.path = Path(
            path or Path(__file__).resolve().parent / "data" / "price-catalog.json"
        ).resolve()
        self.value = json.loads(self.path.read_text(encoding="utf-8"))
        if self.value.get("schema_version") != 1:
            raise ValueError("price catalog has an invalid schema")
        self.models = self.value.get("models", {})
        self.safety_multiplier = float(self.value.get("safety_multiplier", 1.0))
        if self.safety_multiplier < 1.0:
            raise ValueError("price catalog safety multiplier cannot be below one")
        for model, price in self.models.items():
            if not isinstance(model, str) or not isinstance(price, dict):
                raise ValueError("price catalog model entries are invalid")
            if min(
                float(price.get("input_per_million_usd", 0)),
                float(price.get("output_per_million_usd", 0)),
            ) <= 0:
                raise ValueError(f"price catalog entry is incomplete: {model}")

        profiles = self.value.get("planning_profiles", {})
        if set(profiles) != {"planning", "stress"}:
            raise ValueError(
                "price catalog must define planning and stress planning profiles"
            )
        self.planning_profiles: Dict[str, Mapping[str, Any]] = {}
        for name, profile in profiles.items():
            missing = [field for field in _PROFILE_FIELDS if field not in profile]
            if missing:
                raise ValueError(
                    f"price planning profile {name} is missing: {', '.join(missing)}"
                )
            if any(int(profile[field]) <= 0 for field in _PROFILE_FIELDS[:-1]):
                raise ValueError(f"price planning profile {name} has invalid tokens")
            retry_rate = float(profile["solver_retry_rate"])
            if not 0.0 <= retry_rate <= 1.0:
                raise ValueError(
                    f"price planning profile {name} has invalid solver_retry_rate"
                )
            self.planning_profiles[name] = dict(profile)

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    def _price(self, model: str) -> Mapping[str, Any]:
        model_name = str(model).strip()
        price = self.models.get(model_name)
        if not model_name or price is None:
            raise ValueError(f"no verified public price is recorded: {model_name}")
        return price

    def _rates(self, model: str | None) -> tuple[float, float, bool]:
        attribution = current_cost_attribution() if model is None else None
        if attribution is not None and attribution.cost_exempt:
            return 0.0, 0.0, True
        selected = (
            str(model).strip()
            if model is not None
            else attribution.model
            if attribution is not None
            else None
        )
        if selected:
            price = self._price(selected)
            return (
                float(price["input_per_million_usd"]),
                float(price["output_per_million_usd"]),
                False,
            )
        if not self.models:
            raise ValueError("no verified public model prices are recorded")
        # Unattributed legacy operations remain fail-safe rather than cheap.
        return (
            max(float(row["input_per_million_usd"]) for row in self.models.values()),
            max(float(row["output_per_million_usd"]) for row in self.models.values()),
            False,
        )

    def provider_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        model: str | None = None,
    ) -> float:
        """Return frozen provider-rate cost before BBA's safety multiplier."""

        if min(input_tokens, output_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        input_rate, output_rate, exempt = self._rates(model)
        if exempt:
            return 0.0
        return (
            input_tokens / 1_000_000 * input_rate
            + output_tokens / 1_000_000 * output_rate
        )

    def conservative_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str | None = None,
    ) -> float:
        """Return the amount charged to BBA's safety-adjusted hard ledger."""

        return self.safety_multiplier * self.provider_cost(
            input_tokens,
            output_tokens,
            model=model,
        )

    def maximum_conservative_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        models: Iterable[str] | None = None,
    ) -> float:
        selected = tuple(models) if models is not None else tuple(self.models)
        if not selected:
            raise ValueError("no verified public model prices are recorded")
        return max(
            self.conservative_cost(input_tokens, output_tokens, model=model)
            for model in selected
        )

    def _profile_tokens(
        self,
        name: str,
        manifest: ExperimentManifest,
        *,
        creator_runs: int,
        solver_runs: int,
    ) -> tuple[int, int]:
        profile = self.planning_profiles[name]
        session = agent_session_budget(manifest.budget)
        creator_input = min(
            int(profile["creator_input_tokens"]),
            session.max_session_input_tokens,
        )
        creator_output = min(
            int(profile["creator_output_tokens"]),
            session.max_session_output_tokens,
        )
        solver_input = min(
            int(profile["solver_input_tokens"]),
            session.max_session_input_tokens,
        )
        solver_output = min(
            int(profile["solver_output_tokens"]),
            session.max_session_output_tokens,
        )
        retry_multiplier = 1.0 + float(profile["solver_retry_rate"])
        return (
            creator_runs * creator_input
            + math.ceil(solver_runs * retry_multiplier * solver_input),
            creator_runs * creator_output
            + math.ceil(solver_runs * retry_multiplier * solver_output),
        )

    def _cost_view(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Dict[str, Any]:
        provider = self.provider_cost(input_tokens, output_tokens, model=model)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "provider_cost_usd": provider,
            "budgeted_cost_usd": provider * self.safety_multiplier,
        }

    def estimate(self, manifest: ExperimentManifest) -> Dict[str, Any]:
        """Publish uncached planning estimates plus absolute token envelopes."""

        cohort = len(manifest.cohort)
        rounds = manifest.thresholds.rounds
        repetitions = manifest.thresholds.solver_repetitions
        attempts = manifest.retry_policy.max_attempts
        session = agent_session_budget(manifest.budget)

        creator_runs = rounds
        public_solver_runs = rounds * cohort * repetitions
        hidden_solver_runs = 3 * cohort
        solver_runs = public_solver_runs + hidden_solver_runs
        first_runs = creator_runs + solver_runs
        retry_runs = first_runs * attempts

        totals = {
            name: {"provider": 0.0, "budgeted": 0.0}
            for name in ("planning", "stress", "maximum_first", "maximum_retry")
        }
        by_model: Dict[str, Dict[str, Any]] = {}
        complete = True

        for identity in manifest.cohort:
            if identity.model not in self.models:
                by_model[identity.artifact_id] = {
                    "model": identity.model,
                    "runs": retry_runs,
                    "first_attempt_runs": first_runs,
                    "estimate_usd": None,
                    "reason": "no verified public price is recorded",
                }
                complete = False
                continue

            planning = self._cost_view(
                *self._profile_tokens(
                    "planning",
                    manifest,
                    creator_runs=creator_runs,
                    solver_runs=solver_runs,
                ),
                identity.model,
            )
            stress = self._cost_view(
                *self._profile_tokens(
                    "stress",
                    manifest,
                    creator_runs=creator_runs,
                    solver_runs=solver_runs,
                ),
                identity.model,
            )
            maximum_first = self._cost_view(
                first_runs * session.max_session_input_tokens,
                first_runs * session.max_session_output_tokens,
                identity.model,
            )
            maximum_retry = self._cost_view(
                retry_runs * session.max_session_input_tokens,
                retry_runs * session.max_session_output_tokens,
                identity.model,
            )
            views = {
                "planning": planning,
                "stress": stress,
                "maximum_first": maximum_first,
                "maximum_retry": maximum_retry,
            }
            for name, view in views.items():
                totals[name]["provider"] += view["provider_cost_usd"]
                totals[name]["budgeted"] += view["budgeted_cost_usd"]

            by_model[identity.artifact_id] = {
                "model": identity.model,
                "creator_runs": creator_runs,
                "public_solver_runs": public_solver_runs,
                "hidden_solver_runs": hidden_solver_runs,
                "first_attempt_runs": first_runs,
                "maximum_retry_envelope_runs": retry_runs,
                # Backward-compatible name for the complete retry envelope.
                "runs": retry_runs,
                "planning": planning,
                "stress": stress,
                "maximum_first_attempt": maximum_first,
                "maximum_retry_envelope": maximum_retry,
                "estimate_usd": stress["budgeted_cost_usd"],
            }

        def total(name: str, kind: str) -> float | None:
            return totals[name][kind] if complete else None

        stress_total = total("stress", "budgeted")
        hard_limit = float(manifest.budget.max_estimated_cost_usd)
        return {
            "catalog_version": self.value["catalog_version"],
            "catalog_digest": self.digest,
            "effective_date": self.value["effective_date"],
            "safety_multiplier": self.safety_multiplier,
            "estimate_basis": (
                "uncached model-specific rates; planning profiles are versioned "
                "in the frozen price catalog"
            ),
            "complete": complete,
            "planning_provider_cost_usd": total("planning", "provider"),
            "planning_estimate_usd": total("planning", "budgeted"),
            "stress_provider_cost_usd": total("stress", "provider"),
            "stress_estimate_usd": stress_total,
            # Compatibility field: now the uncached stress-planning estimate.
            "conservative_estimate_usd": stress_total,
            "maximum_first_attempt_provider_cost_usd": total(
                "maximum_first", "provider"
            ),
            "maximum_first_attempt_usd": total("maximum_first", "budgeted"),
            "maximum_retry_envelope_provider_cost_usd": total(
                "maximum_retry", "provider"
            ),
            "maximum_retry_envelope_usd": total("maximum_retry", "budgeted"),
            "hard_limit_usd": hard_limit,
            "within_hard_limit": (
                complete and stress_total is not None and stress_total <= hard_limit
            ),
            "models": by_model,
        }

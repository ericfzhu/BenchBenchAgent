"""Versioned local price inputs for conservative epoch estimates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from bba.protocol import ExperimentManifest, digest_json


class PriceCatalog:
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

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    def conservative_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str | None = None,
    ) -> float:
        """Return a frozen upper-bound estimate for one token reservation."""

        if min(input_tokens, output_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if model is not None:
            price = self.models.get(model)
            if price is None:
                raise ValueError(f"no verified public price is recorded: {model}")
            input_rate = float(price["input_per_million_usd"])
            output_rate = float(price["output_per_million_usd"])
        else:
            if not self.models:
                raise ValueError("no verified public model prices are recorded")
            input_rate = max(
                float(value["input_per_million_usd"])
                for value in self.models.values()
            )
            output_rate = max(
                float(value["output_per_million_usd"])
                for value in self.models.values()
            )
        return self.safety_multiplier * (
            input_tokens / 1_000_000 * input_rate
            + output_tokens / 1_000_000 * output_rate
        )

    def estimate(self, manifest: ExperimentManifest) -> Dict[str, Any]:
        cohort = len(manifest.cohort)
        rounds = manifest.thresholds.rounds
        repetitions = manifest.thresholds.solver_repetitions
        attempts = manifest.retry_policy.max_attempts

        # Every model is a creator once per round, solves every public design,
        # and can appear in the committed hidden scaffold. Include the complete
        # retry envelope rather than only the first attempt.
        creator_runs = rounds * attempts
        public_solver_runs = rounds * cohort * repetitions * attempts
        hidden_solver_runs = 3 * cohort * attempts
        runs = creator_runs + public_solver_runs + hidden_solver_runs

        by_model = {}
        total = 0.0
        complete = True
        for identity in manifest.cohort:
            price = self.models.get(identity.model)
            if price is None:
                by_model[identity.artifact_id] = {
                    "runs": runs,
                    "estimate_usd": None,
                    "reason": "no verified public price is recorded",
                }
                complete = False
                continue
            input_tokens = runs * manifest.budget.max_tokens
            output_tokens = runs * manifest.budget.max_tokens
            estimate = self.conservative_cost(
                input_tokens,
                output_tokens,
                model=identity.model,
            )
            total += estimate
            by_model[identity.artifact_id] = {
                "runs": runs,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimate_usd": estimate,
            }
        return {
            "catalog_version": self.value["catalog_version"],
            "catalog_digest": self.digest,
            "effective_date": self.value["effective_date"],
            "safety_multiplier": self.safety_multiplier,
            "complete": complete,
            "conservative_estimate_usd": total if complete else None,
            "hard_limit_usd": manifest.budget.max_estimated_cost_usd,
            "within_hard_limit": (
                complete and total <= manifest.budget.max_estimated_cost_usd
            ),
            "models": by_model,
        }

"""Versioned local price inputs for conservative epoch estimates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

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

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    def estimate(self, manifest: ExperimentManifest) -> Dict[str, Any]:
        cohort = len(manifest.cohort)
        rounds = manifest.thresholds.rounds
        repetitions = manifest.thresholds.solver_repetitions
        creator_runs = rounds
        public_solver_runs = rounds * cohort * repetitions
        hidden_solver_runs = 3 * cohort
        by_model = {}
        total = 0.0
        complete = True
        for identity in manifest.cohort:
            price = self.models.get(identity.model)
            runs = creator_runs + public_solver_runs + hidden_solver_runs
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
            estimate = (
                input_tokens / 1_000_000 * float(price["input_per_million_usd"])
                + output_tokens / 1_000_000 * float(price["output_per_million_usd"])
            )
            total += estimate
            by_model[identity.artifact_id] = {
                "runs": runs,
                "estimate_usd": estimate,
            }
        return {
            "catalog_version": self.value["catalog_version"],
            "catalog_digest": self.digest,
            "effective_date": self.value["effective_date"],
            "complete": complete,
            "conservative_estimate_usd": total if complete else None,
            "hard_limit_usd": manifest.budget.max_estimated_cost_usd,
            "models": by_model,
        }

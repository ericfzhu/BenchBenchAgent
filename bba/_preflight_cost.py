"""Paid readiness check with a frozen, model-specific cost gate."""

from __future__ import annotations

from bba._preflight import *  # noqa: F401,F403
from bba._preflight import run_preflight as _run_preflight
from bba.pricing import PriceCatalog


def run_preflight(manifest, evidence, solver_backends=None):
    if solver_backends is None:
        estimate = PriceCatalog().estimate(manifest)
        if not estimate["complete"]:
            missing = [
                identity
                for identity, row in estimate["models"].items()
                if row["estimate_usd"] is None
            ]
            raise RuntimeError(
                "paid preflight requires verified prices for every route: "
                + ", ".join(missing)
            )
        if not estimate["within_hard_limit"]:
            raise RuntimeError(
                "the uncached stress-planning estimate exceeds the frozen USD "
                f"limit: ${estimate['stress_estimate_usd']:.2f} > "
                f"${estimate['hard_limit_usd']:.2f}. The exact-route runtime "
                "ledger is the authoritative hard stop."
            )
    return _run_preflight(manifest, evidence, solver_backends)

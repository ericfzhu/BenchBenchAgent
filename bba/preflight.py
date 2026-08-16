"""Paid readiness check with frozen cost and live quota gates."""

from __future__ import annotations

from bba._preflight_cost import *  # noqa: F401,F403
from bba._preflight_cost import run_preflight as _run_preflight
from bba.quota import QuotaGovernor


def run_preflight(manifest, evidence, solver_backends=None):
    """Discover effective partner quotas before the first paid model call."""

    if solver_backends is None:
        governor = QuotaGovernor(
            evidence.root,
            manifest.gcp_project,
            location=manifest.gcp_location,
        )
        snapshot = governor.refresh(manifest.cohort, force=True)
        evidence.publish_attempt_record(
            manifest.epoch_id,
            "quota-snapshots",
            "vertex",
            snapshot.to_primitive(),
        )
    return _run_preflight(manifest, evidence, solver_backends)

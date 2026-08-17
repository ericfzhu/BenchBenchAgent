"""Paid readiness check with frozen cost and live quota gates."""

from __future__ import annotations

from bba._preflight import SecureSandbox, build_adk_solver_backends
from bba._preflight_cost import *  # noqa: F401,F403
from bba._preflight_cost import run_preflight as _run_preflight
from bba.quota_project import QuotaGovernor


def run_preflight(manifest, evidence, solver_backends=None):
    """Discover effective partner quotas before the first paid model call."""

    if solver_backends is None:
        sandbox = SecureSandbox(
            memory_mb=manifest.budget.memory_mb,
            process_limit=manifest.budget.process_limit,
            cpu_seconds=manifest.budget.cpu_seconds,
        )
        if not sandbox.available:
            reason = sandbox.unavailable_reason or "unknown sandbox failure"
            raise RuntimeError(f"secure local sandbox is unavailable: {reason}")
        if sandbox.backend != manifest.sandbox.backend:
            raise RuntimeError(
                "local sandbox backend does not match the frozen epoch manifest: "
                f"{sandbox.backend!r} != {manifest.sandbox.backend!r}"
            )
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


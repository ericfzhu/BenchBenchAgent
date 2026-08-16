"""Operator portal facade with effective Vertex quota visibility."""

from __future__ import annotations

import json

from bba._operator_portal import *  # noqa: F401,F403
from bba._operator_portal import OperatorConsole as _OperatorConsole
from bba.catalog import SERVERLESS_COHORT
from bba.gcp import discover_gcp_project
from bba.quota import QuotaGovernor


class OperatorConsole(_OperatorConsole):
    """Development portal API with live model-quota diagnostics."""

    DIAGNOSTIC_ACTIONS = dict(_OperatorConsole.DIAGNOSTIC_ACTIONS) | {
        "quotas": "Inspect effective model quotas",
    }

    def _quota_status(self, *, force: bool = False):
        project = discover_gcp_project()
        governor = QuotaGovernor(self.evidence.root, project, location="global")
        if force:
            governor.refresh(SERVERLESS_COHORT, force=True)
        return governor.status(SERVERLESS_COHORT)

    def readiness(self):
        value = super().readiness()
        try:
            quota = self._quota_status()
            fixed = [row for row in quota["models"] if row["mode"] == "fixed"]
            buckets = {row["bucket"] for row in fixed}
            detail = (
                f"{len(buckets)} fixed/shared buckets · "
                f"{quota['utilization'] * 100:.0f}% BBA utilization target"
            )
            check = {
                "id": "quota",
                "label": "Effective Vertex quotas",
                "status": "passed",
                "detail": detail,
                "required": True,
            }
            value["quota"] = quota
        except Exception as exc:
            check = {
                "id": "quota",
                "label": "Effective Vertex quotas",
                "status": "failed",
                "detail": str(exc),
                "required": True,
            }
            value["quota"] = None
        value["checks"].append(check)
        value["ready"] = all(
            item["status"] == "passed"
            for item in value["checks"]
            if item["required"]
        )
        return value

    def run_diagnostic(self, action: str):
        if action != "quotas":
            return super().run_diagnostic(action)
        return self.jobs.submit(
            self.DIAGNOSTIC_ACTIONS[action],
            None,
            lambda: json.dumps(
                self._quota_status(force=True),
                indent=2,
                sort_keys=True,
            ),
        )

    def epoch(self, epoch_id: str):
        value = super().epoch(epoch_id)
        try:
            value["quota"] = self._quota_status()
        except Exception as exc:
            value["quota"] = {"error": str(exc)}
        return value

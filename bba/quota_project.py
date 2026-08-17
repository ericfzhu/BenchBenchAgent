"""Resolve numeric GCP project resources and grant per-call quota leases."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from google.auth.transport.requests import AuthorizedSession

from bba.quota import (
    CLOUD_SCOPE,
    DEFAULT_UTILIZATION,
    SERVICE_USAGE_URL,
    WINDOW_SECONDS,
    QuotaDiscoveryError,
    QuotaGovernor as _QuotaGovernor,
    VertexQuotaDiscovery as _VertexQuotaDiscovery,
)


RESOURCE_MANAGER_URL = "https://cloudresourcemanager.googleapis.com/v3"
_PROJECT_NUMBER_ENV = (
    "BBA_GCP_PROJECT_NUMBER",
    "GOOGLE_CLOUD_PROJECT_NUMBER",
)


def _project_number_from_environment(explicit: str | None = None) -> str | None:
    value = explicit
    if value is None:
        value = next(
            (
                os.environ[name]
                for name in _PROJECT_NUMBER_ENV
                if os.environ.get(name)
            ),
            None,
        )
    if value is None:
        return None
    normalized = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{5,24}", normalized):
        raise ValueError(
            "GCP project number must contain 6 to 25 digits and cannot start with zero"
        )
    return normalized


class VertexQuotaDiscovery(_VertexQuotaDiscovery):
    """Discover quotas through the numeric project resource required by Service Usage."""

    def __init__(
        self,
        project: str,
        *,
        location: str = "global",
        utilization: float = DEFAULT_UTILIZATION,
        credentials_loader=None,
        session_factory=AuthorizedSession,
        project_number: str | None = None,
    ) -> None:
        super().__init__(
            project,
            location=location,
            utilization=utilization,
            credentials_loader=credentials_loader,
            session_factory=session_factory,
        )
        self.project_number = _project_number_from_environment(project_number)
        self.project_resource: str | None = None

    def _resolve_project_resource(self, session: Any) -> str:
        if self.project_resource is not None:
            return self.project_resource

        if self.project_number is None and str(self.project).isdigit():
            self.project_number = _project_number_from_environment(str(self.project))

        if self.project_number is None:
            try:
                response = session.get(
                    f"{RESOURCE_MANAGER_URL}/projects/{self.project}",
                    timeout=30,
                )
                if response.status_code == 200:
                    payload = response.json()
                    name = str(payload.get("name", ""))
                    if re.fullmatch(r"projects/[1-9][0-9]{5,24}", name):
                        self.project_number = name.split("/", 1)[1]
            except Exception:
                pass

        if self.project_number is not None:
            self.project_resource = f"projects/{self.project_number}"
        else:
            self.project_resource = f"projects/{self.project}"
        return self.project_resource


    def _metrics(self):
        credentials, _ = self.credentials_loader(scopes=[CLOUD_SCOPE])
        session = self.session_factory(credentials)
        project_resource = self._resolve_project_resource(session)
        url = (
            f"{SERVICE_USAGE_URL}/{project_resource}/services/"
            "aiplatform.googleapis.com/consumerQuotaMetrics"
        )
        params = {"view": "FULL", "pageSize": 200}
        result = []
        while True:
            response = session.get(url, params=params, timeout=30)
            if response.status_code >= 400:
                raise QuotaDiscoveryError(
                    "could not read Vertex quotas; grant serviceusage.quotas.get "
                    f"on {self.project}: HTTP {response.status_code} "
                    f"{response.text[:400]}"
                )
            payload = response.json()
            result.extend(payload.get("metrics", ()))
            token = payload.get("nextPageToken")
            if not token:
                return result
            params["pageToken"] = token


@dataclass(frozen=True)
class ModelCallQuotaLease:
    """One atomic model-call admission and its granted output-token cap."""

    lease_id: str
    bucket: str
    mode: str
    estimated_input_tokens: int
    reserved_output_tokens: int

    @property
    def output_cap(self) -> int:
        return self.reserved_output_tokens


class QuotaGovernor(_QuotaGovernor):
    """Project-aware governor that grants output capacity during admission."""

    def __init__(
        self,
        root,
        project,
        *,
        project_number: str | None = None,
        discovery=None,
        **kwargs,
    ) -> None:
        if discovery is None:
            discovery = VertexQuotaDiscovery(
                project,
                location=kwargs.get("location", "global"),
                utilization=kwargs.get("utilization", DEFAULT_UTILIZATION),
                project_number=project_number,
            )
        super().__init__(
            root,
            project,
            discovery=discovery,
            **kwargs,
        )

    def acquire_model_call(
        self,
        identity,
        estimated_input_tokens: int,
        requested_output_tokens: int,
    ) -> ModelCallQuotaLease:
        """Wait for capacity and atomically grant this call's output allowance."""

        estimated_input = int(estimated_input_tokens)
        requested_output = int(requested_output_tokens)
        if estimated_input < 0 or requested_output < 1:
            raise ValueError(
                "model-call quota requests require nonnegative input and positive output"
            )

        policy = self.policy(identity)
        lease_id = uuid4().hex
        while True:
            lease, wait_seconds = self._attempt_model_call(
                policy,
                lease_id,
                estimated_input,
                requested_output,
            )
            if lease is not None:
                return lease
            self.sleeper(min(max(wait_seconds, 0.05), WINDOW_SECONDS))

    def _attempt_model_call(
        self,
        policy,
        lease_id: str,
        estimated_input: int,
        requested_output: int,
    ) -> tuple[ModelCallQuotaLease | None, float]:
        now = self.clock()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM quota_events WHERE project = ? AND started_at <= ?",
                (self.project, now - WINDOW_SECONDS),
            )
            cooldown = db.execute(
                "SELECT until_at FROM quota_cooldowns "
                "WHERE project = ? AND bucket = ?",
                (self.project, policy.bucket),
            ).fetchone()
            if cooldown and float(cooldown["until_at"]) > now:
                db.rollback()
                return None, float(cooldown["until_at"]) - now

            if not policy.fixed:
                db.execute(
                    "INSERT INTO quota_events VALUES "
                    "(?, ?, ?, ?, ?, ?, NULL, NULL, 'in_flight')",
                    (
                        lease_id,
                        self.project,
                        policy.bucket,
                        now,
                        estimated_input,
                        requested_output,
                    ),
                )
                db.commit()
                return ModelCallQuotaLease(
                    lease_id=lease_id,
                    bucket=policy.bucket,
                    mode=policy.mode,
                    estimated_input_tokens=estimated_input,
                    reserved_output_tokens=requested_output,
                ), 0.0

            provider_input = int(policy.provider_input_tokens_per_minute or 0)
            provider_output = int(policy.provider_output_tokens_per_minute or 0)
            target_requests = int(policy.effective_requests_per_minute or 0)
            target_input = int(policy.effective_input_tokens_per_minute or 0)
            target_output = int(policy.effective_output_tokens_per_minute or 0)
            if min(
                provider_input,
                provider_output,
                target_requests,
                target_input,
                target_output,
            ) <= 0:
                db.rollback()
                raise RuntimeError(
                    f"fixed quota policy is incomplete for {policy.bucket}"
                )
            if estimated_input > provider_input:
                db.rollback()
                raise RuntimeError(
                    f"one {policy.bucket} request estimate ({estimated_input}) "
                    f"exceeds provider input TPM ({provider_input})"
                )

            rows = db.execute(
                "SELECT * FROM quota_events "
                "WHERE project = ? AND bucket = ? ORDER BY started_at",
                (self.project, policy.bucket),
            ).fetchall()
            used_input = sum(
                int(
                    row["actual_input"]
                    if row["actual_input"] is not None
                    else row["reserved_input"]
                )
                for row in rows
            )
            used_output = sum(
                int(
                    row["actual_output"]
                    if row["actual_output"] is not None
                    else row["reserved_output"]
                )
                for row in rows
            )
            spacing = (
                max(
                    0.0,
                    float(rows[-1]["started_at"])
                    + float(policy.minimum_spacing_seconds)
                    - now,
                )
                if rows
                else 0.0
            )
            remaining_request_slots = target_requests - len(rows)
            available_output = target_output - used_output

            # Preserve a fair share for every request slot still available in
            # the rolling minute. If earlier calls use less than their grants,
            # later calls can automatically receive the released headroom.
            granted_output = 0
            if remaining_request_slots > 0 and available_output > 0:
                fair_share = max(
                    1,
                    available_output // remaining_request_slots,
                )
                granted_output = min(
                    requested_output,
                    provider_output,
                    available_output,
                    fair_share,
                )

            input_allowed = (
                used_input + estimated_input <= target_input
                or (not rows and estimated_input <= provider_input)
            )
            allowed = (
                remaining_request_slots > 0
                and spacing <= 0.0
                and input_allowed
                and granted_output > 0
            )
            if allowed:
                db.execute(
                    "INSERT INTO quota_events VALUES "
                    "(?, ?, ?, ?, ?, ?, NULL, NULL, 'in_flight')",
                    (
                        lease_id,
                        self.project,
                        policy.bucket,
                        now,
                        estimated_input,
                        granted_output,
                    ),
                )
                db.commit()
                return ModelCallQuotaLease(
                    lease_id=lease_id,
                    bucket=policy.bucket,
                    mode=policy.mode,
                    estimated_input_tokens=estimated_input,
                    reserved_output_tokens=granted_output,
                ), 0.0

            waits = [spacing] if spacing > 0.0 else []
            waits.extend(
                max(
                    0.05,
                    float(row["started_at"]) + WINDOW_SECONDS - now,
                )
                for row in rows
            )
            db.rollback()
            return None, min(waits) if waits else 1.0

    def status(self, identities):
        value = super().status(identities)
        for row in value["models"]:
            limits = row["bba"]
            requests = limits.get("requests_per_minute")
            output = limits.get("output_tokens_per_minute")
            limits["nominal_output_tokens_per_request"] = (
                max(1, int(output) // int(requests))
                if row["mode"] == "fixed" and requests and output
                else None
            )
        return value

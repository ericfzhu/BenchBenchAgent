"""Resolve numeric GCP project resources for Vertex quota governance."""

from __future__ import annotations

import os
import re
from typing import Any

from google.auth.transport.requests import AuthorizedSession

from bba.quota import (
    CLOUD_SCOPE,
    DEFAULT_UTILIZATION,
    SERVICE_USAGE_URL,
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
            response = session.get(
                f"{RESOURCE_MANAGER_URL}/projects/{self.project}",
                timeout=30,
            )
            if response.status_code >= 400:
                raise QuotaDiscoveryError(
                    "could not resolve the numeric GCP project resource; grant "
                    "resourcemanager.projects.get or set BBA_GCP_PROJECT_NUMBER: "
                    f"HTTP {response.status_code} {response.text[:400]}"
                )
            payload = response.json()
            name = str(payload.get("name", ""))
            if not re.fullmatch(r"projects/[1-9][0-9]{5,24}", name):
                raise QuotaDiscoveryError(
                    "Cloud Resource Manager did not return a numeric project resource"
                )
            returned_project_id = payload.get("projectId")
            if (
                returned_project_id
                and str(returned_project_id) != str(self.project)
            ):
                raise QuotaDiscoveryError(
                    "Cloud Resource Manager returned a different GCP project"
                )
            self.project_number = name.split("/", 1)[1]

        self.project_resource = f"projects/{self.project_number}"
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


class QuotaGovernor(_QuotaGovernor):
    """Quota governor that always uses project-number-aware discovery."""

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

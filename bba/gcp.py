"""Local Google Cloud credential and runtime setup."""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, MutableMapping, Optional

import google.auth

from bba.protocol import ExperimentManifest


_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def discover_gcp_project(
    environment: Optional[Mapping[str, str]] = None,
    credentials_loader: Optional[Callable[..., tuple[Any, Optional[str]]]] = None,
) -> str:
    """Find the operator's GCP project from ADC and standard environment data."""

    values = environment if environment is not None else os.environ
    loader = credentials_loader or google.auth.default
    _credentials, adc_project = loader(scopes=[_CLOUD_SCOPE])
    project = values.get("GOOGLE_CLOUD_PROJECT") or adc_project
    if not project:
        raise RuntimeError(
            "ADC did not identify a GCP project; set the ADC quota project or "
            "GOOGLE_CLOUD_PROJECT"
        )
    return project


def configure_gcp_environment(
    manifest: ExperimentManifest,
    environment: Optional[MutableMapping[str, str]] = None,
    credentials_loader: Optional[Callable[..., tuple[Any, Optional[str]]]] = None,
) -> None:
    """Bind ADK to the frozen project and BBA's fixed global GCP location."""

    values = environment if environment is not None else os.environ
    project = discover_gcp_project(values, credentials_loader)
    if project != manifest.gcp_project:
        raise RuntimeError(
            f"ADC project {project!r} does not match frozen epoch project "
            f"{manifest.gcp_project!r}"
        )
    values["GOOGLE_CLOUD_PROJECT"] = manifest.gcp_project
    values["GOOGLE_CLOUD_LOCATION"] = manifest.gcp_location
    values["GOOGLE_GENAI_USE_ENTERPRISE"] = "TRUE"

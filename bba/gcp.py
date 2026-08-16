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
    project = (
        values.get("GOOGLE_CLOUD_PROJECT")
        or values.get("VERTEXAI_PROJECT")
        or adc_project
    )
    if not project:
        raise RuntimeError(
            "Application Default Credentials did not identify a GCP project; "
            "run 'gcloud auth application-default login' and set "
            "GOOGLE_CLOUD_PROJECT"
        )
    return project


def configure_gcp_environment(
    manifest: ExperimentManifest,
    environment: Optional[MutableMapping[str, str]] = None,
    credentials_loader: Optional[Callable[..., tuple[Any, Optional[str]]]] = None,
) -> None:
    """Bind every model adapter to the frozen GCP project and location."""

    values = environment if environment is not None else os.environ
    project = discover_gcp_project(values, credentials_loader)
    if project != manifest.gcp_project:
        raise RuntimeError(
            f"ADC project {project!r} does not match frozen epoch project "
            f"{manifest.gcp_project!r}"
        )

    # Native Google Gen AI and Anthropic-on-Vertex adapters.
    values["GOOGLE_CLOUD_PROJECT"] = manifest.gcp_project
    values["GOOGLE_CLOUD_LOCATION"] = manifest.gcp_location
    values["GOOGLE_GENAI_USE_ENTERPRISE"] = "TRUE"

    # ADK's LiteLlm adapter and LiteLLM read this environment-variable family.
    # The current catalog uses it for the Vertex AI Grok route.
    values["VERTEXAI_PROJECT"] = manifest.gcp_project
    values["VERTEXAI_LOCATION"] = manifest.gcp_location

"""BBA-owned Google Cloud serverless model catalog."""

from __future__ import annotations

from bba.protocol import ModelIdentity, digest_json, to_primitive


CATALOG_VERSION = "gcp-serverless-2026-08-12"
GCP_LOCATION = "global"


def _model(
    publisher: str,
    model: str,
    family: str,
    adk_model: str,
) -> ModelIdentity:
    return ModelIdentity(
        publisher=publisher,
        model=model,
        family=family,
        adk_model=adk_model,
        reasoning="provider-default",
        tools=("function-calling",),
    )


SERVERLESS_COHORT = (
    _model("google", "gemini-3.6-flash", "gemini", "gemini:gemini-3.6-flash"),
    _model("google", "gemini-3.5-flash", "gemini", "gemini:gemini-3.5-flash"),
    _model(
        "google",
        "gemini-3.5-flash-lite",
        "gemini",
        "gemini:gemini-3.5-flash-lite",
    ),
    _model(
        "google",
        "gemini-3.1-pro-preview",
        "gemini",
        "gemini:gemini-3.1-pro-preview",
    ),
    _model("anthropic", "claude-sonnet-5", "claude", "claude:claude-sonnet-5"),
    _model("anthropic", "claude-opus-5", "claude", "claude:claude-opus-5"),
    _model("anthropic", "claude-fable-5", "claude", "claude:claude-fable-5"),
    _model(
        "anthropic",
        "claude-opus-4-8",
        "claude",
        "claude:claude-opus-4-8",
    ),
    _model(
        "anthropic",
        "claude-opus-4-7",
        "claude",
        "claude:claude-opus-4-7",
    ),
    _model(
        "anthropic",
        "claude-sonnet-4-6",
        "claude",
        "claude:claude-sonnet-4-6",
    ),
    _model(
        "anthropic",
        "claude-opus-4-6",
        "claude",
        "claude:claude-opus-4-6",
    ),
    _model("xai", "grok-4.3", "grok", "litellm:vertex_ai/xai/grok-4.3"),
)


CATALOG_DIGEST = digest_json({
    "version": CATALOG_VERSION,
    "location": GCP_LOCATION,
    "cohort": to_primitive(SERVERLESS_COHORT),
})


def catalog_summary() -> dict:
    """Return the immutable public catalog description."""

    return {
        "catalog_version": CATALOG_VERSION,
        "catalog_digest": CATALOG_DIGEST,
        "gcp_location": GCP_LOCATION,
        "models": to_primitive(SERVERLESS_COHORT),
    }

"""BenchBenchAgent public interfaces, loaded only when requested.

Keeping package initialization lightweight lets local diagnostics such as
``bba sandbox-status`` run before optional cloud-provider integrations are
imported.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "AuditStatus": ("bba.protocol", "AuditStatus"),
    "CandidateStatus": ("bba.protocol", "CandidateStatus"),
    "CellState": ("bba.protocol", "CellState"),
    "ExperimentManifest": ("bba.protocol", "ExperimentManifest"),
    "EvidenceStore": ("bba.evidence", "EvidenceStore"),
    "LocalStateStore": ("bba.state", "LocalStateStore"),
    "ModelIdentity": ("bba.protocol", "ModelIdentity"),
    "PromotionDecision": ("bba.protocol", "PromotionDecision"),
    "ReviewFindings": ("bba.protocol", "ReviewFindings"),
    "SolvabilityCertificate": ("bba.protocol", "SolvabilityCertificate"),
    "SolvabilityCertificateType": (
        "bba.protocol",
        "SolvabilityCertificateType",
    ),
    "SolverAttempt": ("bba.protocol", "SolverAttempt"),
    "TournamentController": ("bba.tournament", "TournamentController"),
    "AdkCreatorBackend": ("bba.adk_runtime", "AdkCreatorBackend"),
    "AdkSolverBackend": ("bba.adk_runtime", "AdkSolverBackend"),
    "CATALOG_VERSION": ("bba.catalog", "CATALOG_VERSION"),
    "SERVERLESS_COHORT": ("bba.catalog", "SERVERLESS_COHORT"),
    "audit_evaluator": ("bba.audit", "audit_evaluator"),
    "build_adk_backends": ("bba.adk_runtime", "build_adk_backends"),
    "build_adk_solver_backends": (
        "bba.adk_runtime",
        "build_adk_solver_backends",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

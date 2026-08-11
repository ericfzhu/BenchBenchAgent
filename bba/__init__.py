"""BenchBenchAgent end-state tournament protocol.

The :mod:`bba` package implements a two-sided creator/solver tournament with
immutable evidence, human-gated promotion, a sealed holdout audit, and native
Google ADK creator and solver execution through GCP.
"""

from bba.protocol import (
    AuditStatus,
    CandidateStatus,
    CellState,
    ExperimentManifest,
    ModelIdentity,
    PromotionDecision,
)
from bba.audit import audit_evaluator
from bba.tournament import TournamentController
from bba.adk_runtime import AdkCreatorBackend, AdkSolverBackend, build_adk_backends

__all__ = [
    "AuditStatus",
    "CandidateStatus",
    "CellState",
    "ExperimentManifest",
    "ModelIdentity",
    "PromotionDecision",
    "TournamentController",
    "AdkCreatorBackend",
    "AdkSolverBackend",
    "audit_evaluator",
    "build_adk_backends",
]

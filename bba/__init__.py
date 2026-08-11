"""BenchBenchAgent end-state tournament protocol.

The :mod:`bba` package implements a two-sided creator/solver tournament with
immutable local evidence, restart-safe local state, human-gated promotion, a
sealed holdout audit, and Google ADK inference through Vertex AI.
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
from bba.evidence import EvidenceStore
from bba.state import LocalStateStore

__all__ = [
    "AuditStatus",
    "CandidateStatus",
    "CellState",
    "ExperimentManifest",
    "EvidenceStore",
    "LocalStateStore",
    "ModelIdentity",
    "PromotionDecision",
    "TournamentController",
    "AdkCreatorBackend",
    "AdkSolverBackend",
    "audit_evaluator",
    "build_adk_backends",
]

"""BenchBenchAgent end-state tournament protocol.

The :mod:`bba` package implements a two-sided creator/solver tournament with
immutable evidence, human-gated promotion, and a sealed holdout audit.  The
older top-level modules remain available as a compatibility demo.
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

__all__ = [
    "AuditStatus",
    "CandidateStatus",
    "CellState",
    "ExperimentManifest",
    "ModelIdentity",
    "PromotionDecision",
    "TournamentController",
    "audit_evaluator",
]

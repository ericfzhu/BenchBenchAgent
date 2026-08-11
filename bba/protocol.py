"""Versioned public contracts for BBA epochs and evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


PROTOCOL_VERSION = "bba.epoch.v1"
SCHEMA_VERSION = 1


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class CellState(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    PARTIAL_PREDICTIONS = "partial_predictions"
    PARSE_ERROR = "parse_error"
    SCORER_ERROR = "scorer_error"
    INVALID_BUNDLE = "invalid_bundle"
    NOT_RUN = "not_run"


class CandidateStatus(StrEnum):
    AWAITING_REVIEW = "awaiting_review"
    ACTIVE = "active"
    FRONTIER_CHALLENGE = "frontier_challenge"
    SOLVABILITY_AUDIT = "solvability_audit"
    TOO_EASY = "too_easy"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"
    HISTORICAL = "historical"


class PromotionDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class AuditStatus(StrEnum):
    VALIDATED = "validated"
    UNVALIDATED = "unvalidated"


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
    family: str
    reasoning: str = "default"
    tools: tuple = ()

    def __post_init__(self) -> None:
        for name in ("provider", "model", "family"):
            if not getattr(self, name).strip():
                raise ValueError("model identity fields cannot be blank")

    @property
    def artifact_id(self) -> str:
        raw = f"{self.provider}__{self.model}__{self.reasoning}"
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)


@dataclass(frozen=True)
class ResourceBudget:
    creator_seconds: int = 2400
    solver_seconds: int = 1500
    max_tokens: int = 16000
    memory_mb: int = 2048
    process_limit: int = 64

    def __post_init__(self) -> None:
        if min(
            self.creator_seconds,
            self.solver_seconds,
            self.max_tokens,
            self.memory_mb,
            self.process_limit,
        ) <= 0:
            raise ValueError("resource budgets must be positive")


@dataclass(frozen=True)
class DecisionThresholds:
    sample_count: int = 30
    rounds: int = 3
    solver_repetitions: int = 3
    rejection_accuracy: float = 0.50
    reviewer_sample_count: int = 6
    audit_min_spearman: float = 0.50
    audit_min_pairwise: float = 0.70
    audit_min_utility_recovery: float = 0.90
    audit_min_defect_sensitivity: float = 1.0

    def __post_init__(self) -> None:
        if self.sample_count <= 0 or self.rounds != 3:
            raise ValueError("BBA v1 requires three rounds and a positive sample count")
        if self.solver_repetitions < 1:
            raise ValueError("solver_repetitions must be positive")
        if not 0 < self.rejection_accuracy <= 1:
            raise ValueError("rejection_accuracy must be in (0, 1]")
        if self.reviewer_sample_count != 6 or self.sample_count < 6:
            raise ValueError("BBA v1 requires a six-item human review sample")
        for value in (
            self.audit_min_pairwise,
            self.audit_min_utility_recovery,
            self.audit_min_defect_sensitivity,
        ):
            if not 0 <= value <= 1:
                raise ValueError("audit thresholds must be between zero and one")
        if not -1 <= self.audit_min_spearman <= 1:
            raise ValueError("Spearman threshold must be between -1 and one")


@dataclass(frozen=True)
class SandboxCapabilities:
    network: bool = False
    host_filesystem: bool = False
    ephemeral_home: bool = True
    resource_limits: bool = True
    backend: str = "os"

    def __post_init__(self) -> None:
        if self.network or self.host_filesystem or not self.ephemeral_home:
            raise ValueError("BBA generated-code sandboxes must be credential-free and isolated")


@dataclass(frozen=True)
class ExperimentManifest:
    epoch_id: str
    cohort: tuple
    public_seed: int
    hidden_commitments: Mapping[str, str]
    creator_prompt_digest: str
    solver_prompt_digest: str
    evaluator_version: str
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    sandbox: SandboxCapabilities = field(default_factory=SandboxCapabilities)
    protocol_version: str = PROTOCOL_VERSION
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", self.epoch_id):
            raise ValueError("epoch_id must be a filesystem-safe identifier")
        if len(self.cohort) < 4:
            raise ValueError("an epoch requires at least four model configurations")
        if len({model.family for model in self.cohort}) < 3:
            raise ValueError("an epoch requires at least three model families")
        if len({model.artifact_id for model in self.cohort}) != len(self.cohort):
            raise ValueError("provider-qualified model identities must be unique")
        required = {"hidden_solver_panel", "hidden_seeds", "audit_policy"}
        if set(self.hidden_commitments) != required:
            raise ValueError(f"hidden commitments must be exactly {sorted(required)}")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in self.hidden_commitments.values()):
            raise ValueError("hidden commitments must be lowercase SHA-256 digests")

    @property
    def digest(self) -> str:
        return digest_json(to_primitive(self))


@dataclass(frozen=True)
class ScoreSummary:
    total: int
    correct: int
    accuracy: float
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("score reports must use schema_version 2")
        if self.total <= 0 or not 0 <= self.correct <= self.total:
            raise ValueError("invalid score counts")
        expected = self.correct / self.total
        if abs(self.accuracy - expected) > 1e-9:
            raise ValueError("accuracy is inconsistent with correct / total")


@dataclass(frozen=True)
class SolverCell:
    candidate_digest: str
    solver: ModelIdentity
    repetition: int
    state: CellState
    invocation_digest: str
    score: Optional[ScoreSummary] = None
    prediction_digest: Optional[str] = None
    per_item: Mapping[str, bool] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.repetition < 0:
            raise ValueError("repetition cannot be negative")
        if self.state == CellState.SUCCESS:
            if self.score is None or not self.prediction_digest:
                raise ValueError("successful cells require score and prediction evidence")
            if len(self.per_item) != self.score.total:
                raise ValueError("successful cells require item-level correctness")
            if sum(bool(value) for value in self.per_item.values()) != self.score.correct:
                raise ValueError("item-level results do not match score")
        elif self.score is not None:
            raise ValueError("non-success cell states cannot carry numeric scores")


@dataclass(frozen=True)
class CandidateSnapshot:
    snapshot_id: str
    package_digest: str
    creator: ModelIdentity
    round_index: int
    parent_snapshot_id: Optional[str]
    created_at: str
    package_path: str


@dataclass(frozen=True)
class ValidationRecord:
    candidate_digest: str
    passed: bool
    public_seed: int
    checks: Mapping[str, bool]
    errors: tuple = ()
    generated_payload_digest: Optional[str] = None
    alternate_payload_digest: Optional[str] = None


@dataclass(frozen=True)
class PromotionRecord:
    candidate_digest: str
    reviewer_id: str
    decision: PromotionDecision
    sampled_item_ids: tuple
    reconstructed_answers_digest: str
    evidence_digests: Mapping[str, str]
    limitations: tuple
    timestamp: str
    key_id: str
    signature: str = ""

    def unsigned_payload(self) -> Dict[str, Any]:
        payload = to_primitive(self)
        payload["signature"] = ""
        return payload


def to_primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: to_primitive(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

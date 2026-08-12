"""Versioned public contracts for BBA epochs and evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


PROTOCOL_VERSION = "bba.epoch.v4"
SCHEMA_VERSION = 4


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
    publisher: str
    model: str
    family: str
    adk_model: str
    reasoning: str = "default"
    tools: tuple = ()

    def __post_init__(self) -> None:
        for name in ("publisher", "model", "family", "adk_model"):
            if not getattr(self, name).strip():
                raise ValueError("model identity fields cannot be blank")
        if re.search(r"(^|/)endpoints(/|$)", self.model) or re.match(
            r"https?://", self.model
        ):
            raise ValueError(
                "BBA requires a serverless Vertex AI model ID; deployed and direct endpoints are forbidden"
            )

    @property
    def artifact_id(self) -> str:
        raw = f"gcp__{self.publisher}__{self.model}__{self.reasoning}"
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)


@dataclass(frozen=True)
class ResourceBudget:
    creator_seconds: int = 2400
    solver_seconds: int = 1500
    max_tokens: int = 16000
    max_llm_calls: int = 64
    memory_mb: int = 2048
    process_limit: int = 64
    cpu_seconds: int = 600

    def __post_init__(self) -> None:
        if min(
            self.creator_seconds,
            self.solver_seconds,
            self.max_tokens,
            self.max_llm_calls,
            self.memory_mb,
            self.process_limit,
            self.cpu_seconds,
        ) <= 0:
            raise ValueError("resource budgets must be positive")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retryable_states: tuple = (
        CellState.TIMEOUT.value,
        CellState.PROVIDER_ERROR.value,
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry max_attempts must be positive")
        allowed = {CellState.TIMEOUT.value, CellState.PROVIDER_ERROR.value}
        if set(self.retryable_states) != allowed:
            raise ValueError("BBA v4 retries only timeout and provider_error")


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
    backend: str = "macos-seatbelt"

    def __post_init__(self) -> None:
        if self.network or self.host_filesystem or not self.ephemeral_home:
            raise ValueError("BBA generated-code sandboxes must be credential-free and isolated")
        if self.backend not in {"macos-seatbelt", "trusted-fixture-only"}:
            raise ValueError("BBA requires the local macos-seatbelt sandbox")


@dataclass(frozen=True)
class ExperimentManifest:
    epoch_id: str
    cohort: tuple
    catalog_version: str
    gcp_project: str
    gcp_location: str
    hidden_commitments: Mapping[str, str]
    creator_prompt_digest: str
    solver_prompt_digest: str
    evaluator_version: str
    evaluator_components: Mapping[str, Any] = field(default_factory=dict)
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sandbox: SandboxCapabilities = field(default_factory=SandboxCapabilities)
    protocol_version: str = PROTOCOL_VERSION
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION or self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported epoch contract {self.protocol_version} schema {self.schema_version}; "
                f"expected {PROTOCOL_VERSION} schema {SCHEMA_VERSION}"
            )
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", self.epoch_id):
            raise ValueError("epoch_id must be a filesystem-safe identifier")
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", self.catalog_version):
            raise ValueError("catalog_version must be a stable identifier")
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", self.gcp_project):
            raise ValueError("gcp_project must be a Google Cloud project ID")
        if not re.fullmatch(r"[a-z0-9-]+", self.gcp_location):
            raise ValueError("gcp_location must be a Google Cloud location")
        if len(self.cohort) < 4:
            raise ValueError("an epoch requires at least four model configurations")
        if len({model.family for model in self.cohort}) < 3:
            raise ValueError("an epoch requires at least three model families")
        if len({model.artifact_id for model in self.cohort}) != len(self.cohort):
            raise ValueError("GCP-qualified model identities must be unique")
        required = {"hidden_solver_panel", "hidden_seeds", "audit_policy"}
        if set(self.hidden_commitments) != required:
            raise ValueError(f"hidden commitments must be exactly {sorted(required)}")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in self.hidden_commitments.values()):
            raise ValueError("hidden commitments must be lowercase SHA-256 digests")
        if not re.fullmatch(r"[0-9a-f]{64}", self.evaluator_version):
            raise ValueError("evaluator version must be its lowercase SHA-256 root digest")
        if self.evaluator_components and self.evaluator_components.get("root_digest") != self.evaluator_version:
            raise ValueError("evaluator component identity does not match its root digest")

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
class SolverAttempt:
    attempt_id: str
    cell_id: str
    attempt_index: int
    state: CellState
    invocation_digest: str
    started_at: str
    finished_at: str
    score: Optional[ScoreSummary] = None
    prediction_digest: Optional[str] = None
    per_item: Mapping[str, bool] = field(default_factory=dict)
    evidence_files: Mapping[str, str] = field(default_factory=dict)
    evidence_digests: Mapping[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.attempt_index < 1:
            raise ValueError("solver attempt indexes start at one")
        if not self.attempt_id or not self.cell_id:
            raise ValueError("solver attempt identity cannot be blank")
        if set(self.evidence_files) != set(self.evidence_digests):
            raise ValueError("solver attempt evidence files and digests must match")
        if self.state == CellState.SUCCESS:
            required = {
                "predictions",
                "candidate_scorer_report",
                "controller_scorer_report",
                "command_result",
            }
            if self.score is None or not self.prediction_digest:
                raise ValueError("successful attempts require score and prediction evidence")
            if not required.issubset(self.evidence_files):
                raise ValueError("successful attempts require complete replay evidence")
            if len(self.per_item) != self.score.total:
                raise ValueError("successful attempts require item-level correctness")
            if sum(bool(value) for value in self.per_item.values()) != self.score.correct:
                raise ValueError("attempt item results do not match score")
        elif self.score is not None or self.prediction_digest is not None or self.per_item:
            raise ValueError("non-success attempts cannot carry numeric score evidence")


@dataclass(frozen=True)
class SolverCell:
    snapshot_id: str
    instance_digest: str
    solver: ModelIdentity
    repetition: int
    state: CellState
    invocation_digest: str
    attempt_ids: tuple
    selected_attempt_id: str
    score: Optional[ScoreSummary] = None
    prediction_digest: Optional[str] = None
    per_item: Mapping[str, bool] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.repetition < 0:
            raise ValueError("repetition cannot be negative")
        if not self.attempt_ids or self.selected_attempt_id not in self.attempt_ids:
            raise ValueError("solver cells require one selected immutable attempt")
        if len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise ValueError("solver cell attempt IDs must be unique")
        if self.state == CellState.SUCCESS:
            if self.score is None or not self.prediction_digest:
                raise ValueError("successful cells require score and prediction evidence")
            if len(self.per_item) != self.score.total:
                raise ValueError("successful cells require item-level correctness")
            if sum(bool(value) for value in self.per_item.values()) != self.score.correct:
                raise ValueError("item-level results do not match score")
        elif self.score is not None or self.prediction_digest is not None or self.per_item:
            raise ValueError("non-success cell states cannot carry numeric score evidence")


@dataclass(frozen=True)
class CandidateSnapshot:
    snapshot_id: str
    design_digest: str
    creator: ModelIdentity
    round_index: int
    parent_snapshot_id: Optional[str]
    created_at: str
    design_path: str


@dataclass(frozen=True)
class EvaluationInstance:
    instance_id: str
    snapshot_id: str
    design_digest: str
    instance_digest: str
    round_index: int
    seed: int
    sample_count: int
    created_at: str
    instance_path: str


@dataclass(frozen=True)
class ValidationRecord:
    snapshot_id: str
    design_digest: str
    passed: bool
    evaluation_seed: int
    checks: Mapping[str, bool]
    errors: tuple = ()
    instance_digest: Optional[str] = None
    alternate_payload_digest: Optional[str] = None
    dependency_environment_digest: Optional[str] = None
    dependency_lock_digest: Optional[str] = None
    dependency_catalog_digest: Optional[str] = None


@dataclass(frozen=True)
class ReviewFindings:
    named_capability_valid: bool
    public_materials_sufficient: bool
    oracle_consistent: bool
    scorer_consistent: bool
    no_arbitrary_obscurity: bool
    useful_evaluation: bool

    @property
    def all_passed(self) -> bool:
        return all(to_primitive(self).values())


@dataclass(frozen=True)
class PromotionRecord:
    design_digest: str
    instance_digest: str
    reviewer_id: str
    decision: PromotionDecision
    sampled_item_ids: tuple
    reconstructed_answers_digest: str
    findings: ReviewFindings
    evidence_digests: Mapping[str, str]
    limitations: tuple
    timestamp: str
    key_id: str
    prior_review_digest: Optional[str] = None
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


def model_identity_from_mapping(value: Mapping[str, Any]) -> ModelIdentity:
    """Load one frozen model identity from JSON-compatible data."""

    data = dict(value)
    data["tools"] = tuple(data.get("tools", ()))
    return ModelIdentity(**data)


def experiment_manifest_from_mapping(value: Mapping[str, Any]) -> ExperimentManifest:
    """Load and validate an experiment manifest from JSON-compatible data."""

    data = dict(value)
    data["cohort"] = tuple(model_identity_from_mapping(item) for item in data["cohort"])
    data["thresholds"] = DecisionThresholds(**data.get("thresholds", {}))
    data["budget"] = ResourceBudget(**data.get("budget", {}))
    retry = dict(data.get("retry_policy", {}))
    retry["retryable_states"] = tuple(retry.get("retryable_states", RetryPolicy().retryable_states))
    data["retry_policy"] = RetryPolicy(**retry)
    data["sandbox"] = SandboxCapabilities(**data.get("sandbox", {}))
    return ExperimentManifest(**data)


def score_summary_from_mapping(value: Optional[Mapping[str, Any]]) -> Optional[ScoreSummary]:
    if value is None:
        return None
    return ScoreSummary(**dict(value))


def solver_cell_from_mapping(value: Mapping[str, Any]) -> SolverCell:
    data = dict(value)
    data["solver"] = model_identity_from_mapping(data["solver"])
    data["state"] = CellState(data["state"])
    data["score"] = score_summary_from_mapping(data.get("score"))
    data["attempt_ids"] = tuple(data.get("attempt_ids", ()))
    return SolverCell(**data)


def solver_attempt_from_mapping(value: Mapping[str, Any]) -> SolverAttempt:
    data = dict(value)
    data["state"] = CellState(data["state"])
    data["score"] = score_summary_from_mapping(data.get("score"))
    return SolverAttempt(**data)


def candidate_snapshot_from_mapping(value: Mapping[str, Any]) -> CandidateSnapshot:
    data = dict(value)
    data["creator"] = model_identity_from_mapping(data["creator"])
    return CandidateSnapshot(**data)


def evaluation_instance_from_mapping(value: Mapping[str, Any]) -> EvaluationInstance:
    return EvaluationInstance(**dict(value))


def validation_record_from_mapping(value: Mapping[str, Any]) -> ValidationRecord:
    data = dict(value)
    data["errors"] = tuple(data.get("errors", ()))
    return ValidationRecord(**data)


def promotion_record_from_mapping(value: Mapping[str, Any]) -> PromotionRecord:
    data = dict(value)
    data["decision"] = PromotionDecision(data["decision"])
    data["sampled_item_ids"] = tuple(data.get("sampled_item_ids", ()))
    data["limitations"] = tuple(data.get("limitations", ()))
    data["findings"] = ReviewFindings(**dict(data["findings"]))
    return PromotionRecord(**data)

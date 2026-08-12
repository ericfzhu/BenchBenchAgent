"""Plan and enforce frozen epoch inference limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from bba.protocol import ExperimentManifest


@dataclass(frozen=True)
class EpochEstimate:
    creator_invocations: int
    public_solver_invocations: int
    hidden_solver_invocations: int
    retry_capacity: int
    planned_calls: int
    maximum_output_tokens: int
    hard_call_limit: int
    hard_input_token_limit: int
    hard_output_token_limit: int
    hard_estimated_cost_usd: float


def estimate_epoch(manifest: ExperimentManifest) -> EpochEstimate:
    cohort = len(manifest.cohort)
    rounds = manifest.thresholds.rounds
    repetitions = manifest.thresholds.solver_repetitions
    creator = cohort * rounds
    public_solver = cohort * rounds * cohort * repetitions
    hidden_solver = cohort * cohort * 3
    invocations = creator + public_solver + hidden_solver
    retry_capacity = (
        public_solver + hidden_solver
    ) * (manifest.retry_policy.max_attempts - 1)
    planned_calls = invocations * manifest.budget.max_llm_calls
    maximum_output = invocations * manifest.budget.max_tokens
    return EpochEstimate(
        creator_invocations=creator,
        public_solver_invocations=public_solver,
        hidden_solver_invocations=hidden_solver,
        retry_capacity=retry_capacity,
        planned_calls=planned_calls,
        maximum_output_tokens=maximum_output,
        hard_call_limit=manifest.budget.max_epoch_calls,
        hard_input_token_limit=manifest.budget.max_epoch_input_tokens,
        hard_output_token_limit=manifest.budget.max_epoch_output_tokens,
        hard_estimated_cost_usd=manifest.budget.max_estimated_cost_usd,
    )


class EpochBudgetLedger:
    """Conservative local reservation ledger for model invocations."""

    def __init__(self, manifest: ExperimentManifest):
        self.manifest = manifest
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def reserve(self, max_calls: int, max_input_tokens: int, max_output_tokens: int) -> None:
        budget = self.manifest.budget
        if self.calls + max_calls > budget.max_epoch_calls:
            raise RuntimeError("epoch model-call limit would be exceeded")
        if self.input_tokens + max_input_tokens > budget.max_epoch_input_tokens:
            raise RuntimeError("epoch input-token limit would be exceeded")
        if self.output_tokens + max_output_tokens > budget.max_epoch_output_tokens:
            raise RuntimeError("epoch output-token limit would be exceeded")
        self.calls += max_calls
        self.input_tokens += max_input_tokens
        self.output_tokens += max_output_tokens

    def snapshot(self) -> Dict[str, Any]:
        return {
            "calls_reserved": self.calls,
            "input_tokens_reserved": self.input_tokens,
            "output_tokens_reserved": self.output_tokens,
        }

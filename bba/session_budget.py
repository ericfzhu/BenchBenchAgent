"""One consistent token contract for each creator or solver model session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SESSION_INCREMENTAL_INPUT_RATIO = 8
SESSION_OUTPUT_RATIO = 4
MAX_CONTEXT_RATIO = 64


@dataclass(frozen=True)
class AgentSessionBudget:
    """Per-call, incremental, and cumulative token limits for one ADK agent invocation."""

    max_output_tokens_per_call: int
    max_context_tokens_per_call: int
    max_session_incremental_input_tokens: int
    max_session_output_tokens: int
    max_llm_calls: int

    @property
    def max_session_input_tokens(self) -> int:
        """Compatibility property for max incremental input tokens."""
        return self.max_session_incremental_input_tokens

    def __post_init__(self) -> None:
        if min(
            self.max_output_tokens_per_call,
            self.max_context_tokens_per_call,
            self.max_session_incremental_input_tokens,
            self.max_session_output_tokens,
            self.max_llm_calls,
        ) <= 0:
            raise ValueError("agent session token limits must be positive")
        if self.max_output_tokens_per_call > self.max_session_output_tokens:
            raise ValueError(
                "per-call output limit cannot exceed the session output limit"
            )


def agent_session_budget(resource_budget: Any) -> AgentSessionBudget:
    """Derive the frozen session contract from a manifest resource budget.

    ``max_tokens`` defines the per-turn maximum output token count. Incremental
    input budgeting enforces that net-new content (uncached prompts, files, and tool
    outputs) stays within a bounded 8x envelope (128k tokens for max_tokens=16k),
    while permitting single-turn context windows up to 64x (1,024,000 tokens)
    and session output up to 4x (64,000 tokens).
    """

    max_output = int(resource_budget.max_tokens)
    return AgentSessionBudget(
        max_output_tokens_per_call=max_output,
        max_context_tokens_per_call=max_output * MAX_CONTEXT_RATIO,
        max_session_incremental_input_tokens=max_output * SESSION_INCREMENTAL_INPUT_RATIO,
        max_session_output_tokens=max_output * SESSION_OUTPUT_RATIO,
        max_llm_calls=int(resource_budget.max_llm_calls),
    )


def agent_session_budget_from_values(
    max_tokens: int,
    max_llm_calls: int,
) -> AgentSessionBudget:
    """Build the same contract where only runtime scalar values are available."""

    class _Budget:
        pass

    value = _Budget()
    value.max_tokens = int(max_tokens)
    value.max_llm_calls = int(max_llm_calls)
    return agent_session_budget(value)

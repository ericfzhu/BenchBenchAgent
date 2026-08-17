"""One consistent token contract for each creator or solver model session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SESSION_INPUT_TO_OUTPUT_RATIO = 8


@dataclass(frozen=True)
class AgentSessionBudget:
    """Per-call and cumulative token limits for one ADK agent invocation."""

    max_output_tokens_per_call: int
    max_session_input_tokens: int
    max_session_output_tokens: int
    max_llm_calls: int

    def __post_init__(self) -> None:
        if min(
            self.max_output_tokens_per_call,
            self.max_session_input_tokens,
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

    ``max_tokens`` remains the maximum cumulative output for one creator or
    solver invocation and also caps any individual model turn. Repeated prompt
    prefixes make cumulative input materially larger in tool-using sessions, so
    BBA reserves and enforces an eight-times-larger input envelope.
    """

    max_output = int(resource_budget.max_tokens)
    return AgentSessionBudget(
        max_output_tokens_per_call=max_output,
        max_session_input_tokens=max_output * SESSION_INPUT_TO_OUTPUT_RATIO,
        max_session_output_tokens=max_output,
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

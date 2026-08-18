"""Google ADK runtime with dependency and fail-closed quota governance."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from google.genai import types

from bba import _adk_runtime as _core
from bba._adk_runtime import *  # noqa: F401,F403
from bba.catalog import SERVERLESS_COHORT
from bba.quota_project import ModelCallQuotaLease, QuotaGovernor
from bba.session_budget import agent_session_budget_from_values


_GOVERNED_MODEL_ROUTES = frozenset(
    (identity.publisher, identity.model) for identity in SERVERLESS_COHORT
)

CREATOR_DEPENDENCY_POLICY = """The approved candidate dependency catalog is empty.
Use only the Python standard library. requirements.lock must be empty or contain
comments only. Do not import, vendor, or require any third-party Python package.
The controller will reject a candidate that declares an unavailable dependency."""
CREATOR_INSTRUCTION = (
    _core.CREATOR_INSTRUCTION.rstrip()
    + "\n\n"
    + CREATOR_DEPENDENCY_POLICY
    + "\n"
)


class AdkCreatorBackend(_core.AdkCreatorBackend):
    """Creator backend whose default prompt enforces the frozen wheel policy."""

    def __init__(
        self,
        model,
        *,
        instruction: str = CREATOR_INSTRUCTION,
        construction_sandbox=None,
        max_files: int = 512,
        max_bytes: int = 16 * 1024 * 1024,
        require_usage_metadata: bool = True,
        observability_store=None,
    ) -> None:
        super().__init__(
            model,
            instruction=instruction,
            construction_sandbox=construction_sandbox,
            max_files=max_files,
            max_bytes=max_bytes,
            require_usage_metadata=require_usage_metadata,
            observability_store=observability_store,
        )


def build_adk_backends(
    manifest,
    *,
    construction_sandbox=None,
    creator_instruction: str = CREATOR_INSTRUCTION,
    solver_instruction: str = SOLVER_INSTRUCTION,
    observability_store=None,
):
    """Build the frozen cohort with the dependency-safe creator prompt."""

    return _core.build_adk_backends(
        manifest,
        construction_sandbox=construction_sandbox,
        creator_instruction=creator_instruction,
        solver_instruction=solver_instruction,
        observability_store=observability_store,
    )


# Keep implementation-module lookups aligned with the public facade. The core
# builder resolves these globals at call time.
_core.CREATOR_INSTRUCTION = CREATOR_INSTRUCTION
_core.AdkCreatorBackend = AdkCreatorBackend


class _QuotaObservabilityPlugin(_core._ObservabilityPlugin):
    """Enforce one session token contract and pace every provider model call."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        max_llm_calls = int(kwargs.get("max_llm_calls", 64))
        super().__init__(*args, **kwargs)
        self._session_budget = agent_session_budget_from_values(
            self.per_call_max_tokens,
            max_llm_calls,
        )
        self.session_token_budget = (
            self._session_budget.max_session_incremental_input_tokens
            + self._session_budget.max_session_output_tokens
        )
        store = getattr(self, "store", None)
        governed = (
            self.identity.publisher,
            self.identity.model,
        ) in _GOVERNED_MODEL_ROUTES
        if governed and store is None:
            raise RuntimeError(
                "frozen catalog routes require an evidence-backed quota governor"
            )
        self._quota_governor = (
            QuotaGovernor.from_environment(store.evidence_root)
            if governed
            else None
        )
        self._quota_lease: ModelCallQuotaLease | None = None
        self.peak_context_tokens = 0

    @property
    def incremental_input_tokens(self) -> int:
        return self.peak_context_tokens

    async def before_model_callback(self, *, callback_context, llm_request):
        remaining_incremental_input = (
            self._session_budget.max_session_incremental_input_tokens
            - self.incremental_input_tokens
        )
        remaining_output = (
            self._session_budget.max_session_output_tokens - self.output_tokens
        )
        if remaining_incremental_input <= 0 or remaining_output <= 0:
            raise RuntimeError("frozen ADK session token budget exhausted")

        if llm_request.config is None:
            llm_request.config = types.GenerateContentConfig()
        configured = llm_request.config.max_output_tokens
        requested_output = min(
            int(configured or self._session_budget.max_output_tokens_per_call),
            self._session_budget.max_output_tokens_per_call,
            remaining_output,
        )
        llm_request.config.max_output_tokens = requested_output

        # ADK 2.6.3 handles this setting for Gemini and safely ignores it in
        # adapters that do not implement explicit context caching.
        try:
            from google.adk.models.llm_request import ContextCacheConfig

            if getattr(llm_request, "cache_config", None) is None:
                llm_request.cache_config = ContextCacheConfig(
                    cache_intervals=1,
                    ttl_seconds=3600,
                    min_tokens=1024,
                    create_http_options=None,
                )
        except (ImportError, TypeError, AttributeError):
            pass

        if self._quota_governor is not None:
            if self._quota_lease is not None:
                raise RuntimeError(
                    "the previous model quota lease was not reconciled"
                )
            estimated_input = self._quota_governor.estimate_input_tokens(
                llm_request
            )
            if estimated_input > self._session_budget.max_context_tokens_per_call:
                raise RuntimeError(
                    "the next model call would exceed the model context limit"
                )
            lease = await asyncio.to_thread(
                self._quota_governor.acquire_model_call,
                self.identity,
                estimated_input,
                requested_output,
            )
            llm_request.config.max_output_tokens = min(
                requested_output,
                lease.output_cap,
            )
            self._quota_lease = lease

        # Quota waiting is infrastructure time and is deliberately excluded
        # from provider model latency.
        self.model_calls += 1
        self._model_started_ns.append(time.monotonic_ns())
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        usage = llm_response.usage_metadata
        input_tokens = (
            int(getattr(usage, "prompt_token_count", 0) or 0)
            if usage is not None
            else 0
        )
        output_tokens = (
            int(getattr(usage, "candidates_token_count", 0) or 0)
            if usage is not None
            else 0
        )
        if input_tokens > self.peak_context_tokens:
            self.peak_context_tokens = input_tokens
        try:
            result = await super().after_model_callback(
                callback_context=callback_context,
                llm_response=llm_response,
            )
            if output_tokens > self._session_budget.max_output_tokens_per_call:
                raise RuntimeError("frozen ADK per-call output limit exceeded")
            if (
                self.incremental_input_tokens
                > self._session_budget.max_session_incremental_input_tokens
                or self.output_tokens
                > self._session_budget.max_session_output_tokens
            ):
                raise RuntimeError("frozen ADK session token budget exceeded")
            return result
        finally:
            if self._quota_governor is not None and self._quota_lease:
                lease = self._quota_lease
                self._quota_lease = None
                await asyncio.to_thread(
                    self._quota_governor.reconcile,
                    lease.lease_id,
                    input_tokens,
                    output_tokens,
                )

    async def on_model_error_callback(
        self,
        *,
        callback_context,
        llm_request,
        error,
    ):
        if self._quota_governor is not None and self._quota_lease:
            lease = self._quota_lease
            self._quota_lease = None
            await asyncio.to_thread(
                self._quota_governor.fail,
                lease.lease_id,
                error,
            )
        return await super().on_model_error_callback(
            callback_context=callback_context,
            llm_request=llm_request,
            error=error,
        )

    def finish(self, status: str, error: BaseException | None) -> None:
        if self._quota_governor is not None and self._quota_lease:
            lease = self._quota_lease
            self._quota_lease = None
            self._quota_governor.fail(
                lease.lease_id,
                error
                or RuntimeError(
                    "model call ended before quota usage was reconciled"
                ),
            )
        super().finish(status, error)


# _run_agent lives in the implementation module and resolves this global at
# call time. Replacing it here applies the contract to creator, solver,
# preflight, and sealed-audit model turns without duplicating backend logic.
_core._ObservabilityPlugin = _QuotaObservabilityPlugin
_ObservabilityPlugin = _QuotaObservabilityPlugin


def __getattr__(name: str) -> Any:
    return getattr(_core, name)

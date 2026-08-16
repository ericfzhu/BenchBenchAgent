"""Google ADK runtime with project-aware Vertex quota governance."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from google.genai import types

from bba import _adk_runtime as _core
from bba._adk_runtime import *  # noqa: F401,F403
from bba.quota_project import QuotaGovernor


class _QuotaObservabilityPlugin(_core._ObservabilityPlugin):
    """Apply model budgets and acquire quota capacity before each model call."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        store = getattr(self, "store", None)
        self._quota_governor = (
            QuotaGovernor.from_environment(store.evidence_root)
            if store is not None
            else None
        )
        self._quota_lease: str | None = None

    async def before_model_callback(self, *, callback_context, llm_request):
        remaining = self.token_budget - self.total_tokens
        if remaining <= 0:
            raise RuntimeError("frozen ADK token budget exhausted")
        if llm_request.config is None:
            llm_request.config = types.GenerateContentConfig(
                max_output_tokens=remaining
            )
        else:
            configured = llm_request.config.max_output_tokens
            llm_request.config.max_output_tokens = min(
                configured or remaining,
                remaining,
            )
        for name in ("temperature", "top_p"):
            value = self.behavior_settings.get(name)
            if value is not None:
                setattr(llm_request.config, name, value)

        if self._quota_governor is not None:
            estimated_input = self._quota_governor.estimate_input_tokens(
                llm_request
            )
            requested_output = int(
                llm_request.config.max_output_tokens or remaining
            )
            output_cap = self._quota_governor.output_cap(
                self.identity,
                requested_output,
            )
            llm_request.config.max_output_tokens = output_cap
            self._quota_lease = await asyncio.to_thread(
                self._quota_governor.acquire,
                self.identity,
                estimated_input,
                output_cap,
            )

        # Quota waiting is infrastructure time, not model latency.
        self.model_calls += 1
        self._model_started_ns.append(time.monotonic_ns())
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        usage = llm_response.usage_metadata
        input_tokens = int(
            getattr(usage, "prompt_token_count", 0) or 0
        ) if usage is not None else 0
        output_tokens = int(
            getattr(usage, "candidates_token_count", 0) or 0
        ) if usage is not None else 0
        try:
            return await super().after_model_callback(
                callback_context=callback_context,
                llm_response=llm_response,
            )
        finally:
            if self._quota_governor is not None and self._quota_lease:
                lease = self._quota_lease
                self._quota_lease = None
                await asyncio.to_thread(
                    self._quota_governor.reconcile,
                    lease,
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
                lease,
                error,
            )
        return await super().on_model_error_callback(
            callback_context=callback_context,
            llm_request=llm_request,
            error=error,
        )

    def finish(self, status: str, error: BaseException | None) -> None:
        if (
            error is not None
            and self._quota_governor is not None
            and self._quota_lease
        ):
            self._quota_governor.fail(self._quota_lease, error)
            self._quota_lease = None
        super().finish(status, error)


# _run_agent lives in the implementation module and resolves this global at
# call time. Replacing it here applies the governor to creator, solver, preflight,
# and sealed-audit model turns without duplicating backend logic.
_core._ObservabilityPlugin = _QuotaObservabilityPlugin
_ObservabilityPlugin = _QuotaObservabilityPlugin

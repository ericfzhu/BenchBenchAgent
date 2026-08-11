"""Native Google ADK 2.6.3 execution tests with a deterministic BaseLlm."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import Field

from bba.adk_runtime import (
    ADK_VERSION,
    CREATOR_INSTRUCTION,
    SOLVER_INSTRUCTION,
    AdkCreatorBackend,
    AdkSolverBackend,
)
from bba.protocol import ExperimentManifest, ModelIdentity, digest_json


class ScriptedLlm(BaseLlm):
    responses: list[LlmResponse] = Field(default_factory=list)
    cursor: int = 0

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.cursor >= len(self.responses):
            raise RuntimeError("scripted ADK model exhausted")
        response = self.responses[self.cursor]
        self.cursor += 1
        yield response


def _tool_call(call_id: str, name: str, args: dict) -> LlmResponse:
    return LlmResponse(content=types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(
            id=call_id,
            name=name,
            args=args,
        ))],
    ))


def _final(text: str = "complete") -> LlmResponse:
    return LlmResponse(content=types.Content(
        role="model",
        parts=[types.Part(text=text)],
    ))


class TestAdkRuntime(unittest.TestCase):
    def setUp(self):
        self.identity = ModelIdentity("google", "scripted", "family-a")
        cohort = (
            self.identity,
            ModelIdentity("provider-b", "b", "family-b"),
            ModelIdentity("provider-c", "c", "family-c"),
            ModelIdentity("provider-a", "d", "family-a"),
        )
        self.manifest = ExperimentManifest(
            epoch_id="adk-runtime-test",
            cohort=cohort,
            public_seed=42,
            hidden_commitments={
                "hidden_solver_panel": digest_json(["sealed-solver"]),
                "hidden_seeds": digest_json([43]),
                "audit_policy": digest_json({"version": 1}),
            },
            creator_prompt_digest=digest_json(CREATOR_INSTRUCTION),
            solver_prompt_digest=digest_json(SOLVER_INSTRUCTION),
            evaluator_version="test",
        )

    def test_exact_stable_adk_release_is_loaded(self):
        self.assertEqual(ADK_VERSION, "2.6.3")

    def test_creator_uses_adk_tools_and_emits_redacted_trace(self):
        model = ScriptedLlm(model="scripted-creator", responses=[
            _tool_call("write-1", "write_candidate_file", {
                "path": "README.md",
                "content": "# Native ADK candidate\n",
            }),
            _tool_call("finish-1", "finish_candidate", {}),
            _final(),
        ])
        backend = AdkCreatorBackend(model)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            backend.build(
                self.identity,
                0,
                output,
                {},
                None,
                self.manifest,
            )
            self.assertEqual(
                (output / "README.md").read_text(encoding="utf-8"),
                "# Native ADK candidate\n",
            )
        trace = backend.take_trace()
        self.assertIsNotNone(trace)
        self.assertEqual(trace.adk_version, "2.6.3")
        self.assertEqual(trace.model_calls, 3)
        self.assertEqual(
            trace.tool_calls,
            ("write_candidate_file", "finish_candidate"),
        )
        self.assertEqual(trace.status, "success")
        self.assertTrue(trace.final_response_digest)

    def test_solver_requires_explicit_complete_tool_submission(self):
        rows = [
            {"id": "item-1", "answer": 11},
            {"id": "item-2", "answer": {"value": "x"}},
        ]
        model = ScriptedLlm(model="scripted-solver", responses=[
            _tool_call("submit-1", "submit_predictions", {
                "predictions_json": json.dumps(rows),
            }),
            _final(),
        ])
        backend = AdkSolverBackend(model)
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / "README.md").write_text("public", encoding="utf-8")
            predictions = backend.solve(
                self.identity,
                bundle,
                [{"id": "item-1"}, {"id": "item-2"}],
                0,
                self.manifest,
            )
        self.assertEqual(predictions, rows)
        trace = backend.take_trace()
        self.assertEqual(trace.tool_calls, ("submit_predictions",))
        self.assertEqual(trace.model_calls, 2)
        self.assertTrue(trace.session_id.startswith("solver-"))


if __name__ == "__main__":
    unittest.main()

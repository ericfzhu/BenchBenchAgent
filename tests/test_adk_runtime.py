"""Native Google ADK 2.6.3 execution tests with a deterministic BaseLlm."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
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
    resolve_model,
)
from bba.catalog import CATALOG_VERSION, SERVERLESS_COHORT
from bba.errors import ProviderFailure
from bba.gcp import configure_gcp_environment
from bba.protocol import ExperimentManifest, ModelIdentity, ResourceBudget, digest_json


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
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(
                id=call_id,
                name=name,
                args=args,
            ))],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1,
            candidates_token_count=1,
            total_token_count=2,
        ),
    )


def _final(text: str = "complete") -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text=text)],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1,
            candidates_token_count=1,
            total_token_count=2,
        ),
    )


class TestAdkRuntime(unittest.TestCase):
    def setUp(self):
        self.identity = ModelIdentity(
            "google", "scripted", "family-a", "gemini:scripted"
        )
        cohort = (
            self.identity,
            ModelIdentity("meta", "b", "family-b", "litellm:vertex_ai/meta/b"),
            ModelIdentity(
                "mistral", "c", "family-c", "litellm:vertex_ai/mistral/c"
            ),
            ModelIdentity("google", "d", "family-a", "gemini:d"),
        )
        self.manifest = ExperimentManifest(
            epoch_id="adk-runtime-test",
            cohort=cohort,
            catalog_version="test-catalog",
            gcp_project="bba-test-project",
            gcp_location="global",
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

    def test_model_resolution_stays_on_google_cloud(self):
        self.assertEqual(resolve_model(self.identity).model, "scripted")
        open_model = resolve_model(ModelIdentity(
            "meta", "llama", "llama", "litellm:vertex_ai/meta/llama"
        ))
        self.assertEqual(open_model.model, "vertex_ai/meta/llama")

    def test_every_built_in_catalog_route_resolves_in_adk(self):
        self.assertEqual(CATALOG_VERSION, "gcp-serverless-2026-08-12")
        for identity in SERVERLESS_COHORT:
            with self.subTest(model=identity.model):
                expected = identity.adk_model.split(":", 1)[1]
                self.assertEqual(resolve_model(identity).model, expected)

    def test_gcp_environment_must_match_manifest(self):
        environment = {}
        loader = lambda **_kwargs: (object(), self.manifest.gcp_project)
        configure_gcp_environment(self.manifest, environment, loader)
        self.assertEqual(environment["GOOGLE_CLOUD_PROJECT"], "bba-test-project")
        self.assertEqual(environment["GOOGLE_CLOUD_LOCATION"], "global")
        self.assertEqual(environment["GOOGLE_GENAI_USE_ENTERPRISE"], "TRUE")
        with self.assertRaises(RuntimeError):
            configure_gcp_environment(
                self.manifest,
                {},
                lambda **_kwargs: (object(), "wrong-project"),
            )

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
        self.assertTrue(trace.usage_metadata_complete)
        self.assertEqual(trace.total_tokens, 6)
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

    def test_cumulative_token_budget_is_enforced(self):
        model = ScriptedLlm(model="scripted-budget", responses=[
            _tool_call("write-1", "write_candidate_file", {
                "path": "README.md",
                "content": "over budget",
            }),
        ])
        backend = AdkCreatorBackend(model)
        constrained = replace(
            self.manifest,
            budget=ResourceBudget(max_tokens=1),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ProviderFailure):
                backend.build(
                    self.identity,
                    0,
                    Path(temporary),
                    {},
                    None,
                    constrained,
                )
        trace = backend.take_trace()
        self.assertEqual(trace.status, "provider_error")
        self.assertEqual(trace.total_tokens, 2)


if __name__ == "__main__":
    unittest.main()

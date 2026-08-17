"""Solver tool submissions can self-correct within one ADK trajectory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import Field

from bba.adk_runtime import AdkSolverBackend
from bba.protocol import ModelIdentity
from tests.test_end_state_protocol import manifest


class ScriptedLlm(BaseLlm):
    responses: list[LlmResponse] = Field(default_factory=list)
    cursor: int = 0

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.cursor >= len(self.responses):
            raise RuntimeError("scripted model exhausted")
        response = self.responses[self.cursor]
        self.cursor += 1
        yield response


def tool_call(call_id: str, name: str, args: dict) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id=call_id,
                        name=name,
                        args=args,
                    )
                )
            ],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10,
            candidates_token_count=2,
            total_token_count=12,
        ),
        model_version="scripted-self-correction-v1",
    )


def final_response() -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="complete")],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10,
            candidates_token_count=1,
            total_token_count=11,
        ),
        model_version="scripted-self-correction-v1",
    )


def valid_debrief(item_ids: list[str]) -> dict:
    return {
        "schema_version": 1,
        "items": [
            {
                "item_id": item_id,
                "confidence": 0.8,
                "approach_tags": ["self_correction"],
                "evidence_refs": ["README.md"],
                "concise_justification": "Corrected the submission after reading the tool error.",
                "uncertainties": [],
                "missing_information": [],
            }
            for item_id in item_ids
        ],
    }


class TestSolverSelfCorrection(unittest.TestCase):
    def setUp(self):
        self.identity = ModelIdentity(
            "test",
            "scripted-self-correction",
            "test",
            "litellm:test/scripted-self-correction",
        )
        self.manifest = manifest("solver-self-correction")

    def solve(self, responses, items):
        backend = AdkSolverBackend(
            ScriptedLlm(
                model="scripted-self-correction",
                responses=responses,
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("public evidence", encoding="utf-8")
            predictions = backend.solve(
                self.identity,
                root,
                items,
                0,
                self.manifest,
            )
        return backend, predictions

    def test_malformed_predictions_and_debrief_are_corrected(self):
        items = [{"id": "item-a"}, {"id": "item-b"}]
        expected = [
            {"id": "item-a", "answer": 11},
            {"id": "item-b", "answer": "x"},
        ]
        backend, predictions = self.solve(
            [
                tool_call(
                    "pred-invalid-json",
                    "submit_predictions",
                    {"predictions_json": "{"},
                ),
                tool_call(
                    "pred-missing-id",
                    "submit_predictions",
                    {
                        "predictions_json": json.dumps(
                            [{"id": "item-a", "answer": 11}]
                        )
                    },
                ),
                tool_call(
                    "pred-corrected",
                    "submit_predictions",
                    {"predictions_json": json.dumps(expected)},
                ),
                tool_call(
                    "debrief-incomplete",
                    "submit_debrief",
                    {"debrief_json": json.dumps(valid_debrief(["item-a"]))},
                ),
                tool_call(
                    "debrief-corrected",
                    "submit_debrief",
                    {
                        "debrief_json": json.dumps(
                            valid_debrief(["item-a", "item-b"])
                        )
                    },
                ),
                final_response(),
            ],
            items,
        )
        self.assertEqual(predictions, expected)
        self.assertEqual(
            [item.item_id for item in backend.take_debrief().items],
            ["item-a", "item-b"],
        )
        trace = backend.take_trace()
        self.assertEqual(
            trace.tool_calls,
            (
                "submit_predictions",
                "submit_predictions",
                "submit_predictions",
                "submit_debrief",
                "submit_debrief",
            ),
        )
        self.assertEqual(trace.model_calls, 6)
        self.assertTrue(trace.usage_metadata_complete)

    def test_early_debrief_can_be_followed_by_valid_submissions(self):
        items = [{"id": "item-a"}]
        backend, predictions = self.solve(
            [
                tool_call(
                    "debrief-early",
                    "submit_debrief",
                    {"debrief_json": json.dumps(valid_debrief(["item-a"]))},
                ),
                tool_call(
                    "predictions",
                    "submit_predictions",
                    {"predictions_json": {"answer": 7}},
                ),
                tool_call(
                    "debrief",
                    "submit_debrief",
                    {
                        "debrief_json": {
                            "confidence": 0.9,
                            "approach_tags": ["single_item"],
                            "evidence_refs": [],
                            "concise_justification": "Used the declared single-item rule.",
                            "uncertainties": [],
                            "missing_information": [],
                        }
                    },
                ),
                final_response(),
            ],
            items,
        )
        self.assertEqual(predictions, [{"id": "item-a", "answer": 7}])
        self.assertEqual(backend.take_debrief().items[0].item_id, "item-a")
        self.assertEqual(
            backend.take_trace().tool_calls,
            ("submit_debrief", "submit_predictions", "submit_debrief"),
        )

    def test_duplicate_prediction_submission_does_not_unlock_answers(self):
        items = [{"id": "item-a"}]
        backend, predictions = self.solve(
            [
                tool_call(
                    "predictions",
                    "submit_predictions",
                    {"predictions_json": {"item-a": 3}},
                ),
                tool_call(
                    "duplicate",
                    "submit_predictions",
                    {"predictions_json": {"item-a": 999}},
                ),
                tool_call(
                    "debrief",
                    "submit_debrief",
                    {"debrief_json": json.dumps(valid_debrief(["item-a"]))},
                ),
                final_response(),
            ],
            items,
        )
        self.assertEqual(predictions, [{"id": "item-a", "answer": 3}])
        self.assertEqual(backend.take_debrief().items[0].item_id, "item-a")


if __name__ == "__main__":
    unittest.main()

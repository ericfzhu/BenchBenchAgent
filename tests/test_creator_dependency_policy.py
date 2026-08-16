"""Creator prompt dependency-policy tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bba.adk_runtime import (
    AdkCreatorBackend,
    CREATOR_DEPENDENCY_POLICY,
    CREATOR_INSTRUCTION,
    build_adk_backends,
)
from bba.protocol import digest_json


class TestCreatorDependencyPolicy(unittest.TestCase):
    def test_default_creator_prompt_forbids_third_party_dependencies(self):
        self.assertIn(
            "approved candidate dependency catalog is empty",
            CREATOR_INSTRUCTION,
        )
        self.assertIn(
            "Use only the Python standard library",
            CREATOR_INSTRUCTION,
        )
        self.assertIn(
            "requirements.lock must be empty",
            CREATOR_INSTRUCTION,
        )
        self.assertIn(
            "Do not import, vendor, or require",
            CREATOR_DEPENDENCY_POLICY,
        )

        backend = AdkCreatorBackend(
            object(),
            construction_sandbox=SimpleNamespace(),
        )
        self.assertEqual(backend.instruction, CREATOR_INSTRUCTION)
        self.assertEqual(
            backend.prompt_digest,
            digest_json(CREATOR_INSTRUCTION),
        )

    def test_public_builder_passes_dependency_safe_prompt_to_core(self):
        with patch(
            "bba.adk_runtime._core.build_adk_backends",
            return_value=({}, {}),
        ) as core:
            result = build_adk_backends(
                SimpleNamespace(),
                construction_sandbox=SimpleNamespace(),
            )

        self.assertEqual(result, ({}, {}))
        self.assertEqual(
            core.call_args.kwargs["creator_instruction"],
            CREATOR_INSTRUCTION,
        )


if __name__ == "__main__":
    unittest.main()

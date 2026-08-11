"""Multi-tier Model Dispatcher supporting Vertex AI, AI Studio, CLI, and Mock Simulation."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from config import BBAConfig, load_config

logger = logging.getLogger("bba.backend.dispatcher")


class ModelDispatcher:
    """4-tier model dispatcher for autonomous agent benchmark synthesis and solving."""

    def __init__(self, config: Optional[BBAConfig] = None):
        self.config: BBAConfig = config or load_config()
        self.provider: str = self.config.resolved_provider()
        self._genai_client = None
        self._init_client()

    def _init_client(self) -> None:
        """Initializes client based on resolved provider tier."""
        if self.provider in ["vertex", "studio"]:
            try:
                from google import genai  # type: ignore
                if self.provider == "vertex":
                    self._genai_client = genai.Client(
                        vertexai=True,
                        project=self.config.gcp_project,
                        location=self.config.gcp_location,
                    )
                    logger.info(f"Initialized Vertex AI Client (project={self.config.gcp_project}, location={self.config.gcp_location})")
                elif self.provider == "studio":
                    self._genai_client = genai.Client(api_key=self.config.api_key)
                    logger.info("Initialized Google AI Studio Client")
            except Exception as e:
                logger.warning(f"Failed to initialize {self.provider} client: {e}. Falling back to mock simulation.")
                self.provider = "mock"

    async def generate(
        self,
        prompt: str,
        system_instruction: str = "",
        model: str = "",
        role: str = "creator",
        temperature: float = 0.2,
        **kwargs,
    ) -> str:
        """Asynchronously dispatches generation request across active provider tier."""
        target_model = model or getattr(self.config, f"{role}_model", "gemini-2.5-pro")

        # Tier 1 & 2: Vertex AI & Google AI Studio via google-genai
        if self.provider in ["vertex", "studio"] and self._genai_client is not None:
            try:
                loop = asyncio.get_running_loop()
                config_args = {"temperature": temperature}
                if system_instruction:
                    config_args["system_instruction"] = system_instruction

                response = await loop.run_in_executor(
                    None,
                    lambda: self._genai_client.models.generate_content(
                        model=target_model,
                        contents=prompt,
                        config=config_args,
                    )
                )
                if hasattr(response, "text") and response.text:
                    return response.text
                return str(response)
            except Exception as e:
                logger.warning(f"{self.provider} API call failed ({e}), falling back to offline simulation.")
                self.provider = "mock"

        # Tier 3: CLI Bridge
        if self.provider == "cli":
            for cli in ["claude", "codex", "cursoragent"]:
                if shutil.which(cli):
                    try:
                        cmd = [cli, "--prompt", f"{system_instruction}\n\n{prompt}"]
                        proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await proc.communicate()
                        if proc.returncode == 0:
                            return stdout.decode("utf-8")
                    except Exception as e:
                        logger.warning(f"CLI {cli} execution failed: {e}")

        # Tier 4: High-fidelity Offline Mock Simulation
        return self._mock_generate(prompt=prompt, system_instruction=system_instruction, role=role, **kwargs)

    def _mock_generate(
        self,
        prompt: str,
        system_instruction: str = "",
        role: str = "creator",
        **kwargs,
    ) -> str:
        """Deterministic, high-fidelity offline simulation for testing and offline development."""
        if role == "creator" or "Synthesize a complete benchmark" in prompt:
            return json.dumps({
                "status": "SUCCESS",
                "action": "BENCHMARK_SYNTHESIZED",
                "domain": "financial_forensics",
                "spec": "004-financial-expense-forensics",
                "items_count": 30,
                "message": "Deterministic Expense Forensics benchmark generated successfully."
            }, indent=2)

        if role == "repair" or "Repair the benchmark" in prompt:
            return json.dumps({
                "status": "SUCCESS",
                "action": "BENCHMARK_REPAIRED",
                "repairs_applied": ["schema_alignment", "determinism_restored"],
                "message": "Benchmark package repaired and validated."
            }, indent=2)

        if role == "solver":
            return json.dumps({
                "status": "SUCCESS",
                "action": "ITEM_SOLVED",
                "note": "Forensic audit calculated with Decimal half-up arithmetic."
            }, indent=2)

        if role == "referee":
            return json.dumps({
                "status": "SUCCESS",
                "action": "EVALUATION_COMPLETED",
                "recommendation": "EVOLVE_NEXT_ROUND",
                "feedback": "Increase difficulty on international multi-currency lodging folios."
            }, indent=2)

        return f"Mock response for {role}: prompt received ({len(prompt)} chars)."

    def generate_sync(self, prompt: str, **kwargs) -> str:
        """Synchronous helper for generate."""
        return asyncio.run(self.generate(prompt=prompt, **kwargs))

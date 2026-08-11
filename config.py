"""Configuration models and environment loading for BenchBenchAgent (BBA)."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    from pydantic import BaseModel, Field  # type: ignore
    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False


@dataclass
class BBAConfig:
    """Runtime configuration for BBA co-evolution adversarial workflows."""

    provider: str = field(
        default_factory=lambda: os.getenv("BBA_PROVIDER", "auto").lower()
    )
    gcp_project: Optional[str] = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    )
    gcp_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    )
    api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    )
    creator_model: str = field(
        default_factory=lambda: os.getenv("BBA_CREATOR_MODEL", "gemini-2.5-pro")
    )
    solver_model: str = field(
        default_factory=lambda: os.getenv("BBA_SOLVER_MODEL", "gemini-2.5-flash")
    )
    repair_model: str = field(
        default_factory=lambda: os.getenv("BBA_REPAIR_MODEL", "gemini-2.5-pro")
    )
    referee_model: str = field(
        default_factory=lambda: os.getenv("BBA_REFEREE_MODEL", "gemini-2.5-pro")
    )
    domain: str = field(
        default_factory=lambda: os.getenv("BBA_DOMAIN", "financial_forensics")
    )
    max_rounds: int = 3
    target_discriminative_min: int = 10
    target_discriminative_max: int = 18
    total_items: int = 30
    timeout_seconds: int = 60
    scratch_dir: str = field(
        default_factory=lambda: os.getenv("BBA_SCRATCH_DIR", "/tmp/bba_workspace")
    )

    def resolved_provider(self) -> str:
        """Determines the active provider based on environment and availability."""
        if self.provider != "auto":
            return self.provider

        # Check Vertex AI prerequisites
        if self.gcp_project:
            try:
                import google.genai  # type: ignore
                return "vertex"
            except ImportError:
                pass

        # Check Google AI Studio API Key
        if self.api_key:
            try:
                import google.genai  # type: ignore
                return "studio"
            except ImportError:
                pass

        # Check CLI availability
        import shutil
        for cli in ["claude", "codex", "cursoragent"]:
            if shutil.which(cli):
                return "cli"

        # Fallback to high-fidelity offline mock simulation
        return "mock"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "resolved_provider": self.resolved_provider(),
            "gcp_project": self.gcp_project,
            "gcp_location": self.gcp_location,
            "api_key": "***" if self.api_key else None,
            "creator_model": self.creator_model,
            "solver_model": self.solver_model,
            "repair_model": self.repair_model,
            "referee_model": self.referee_model,
            "domain": self.domain,
            "max_rounds": self.max_rounds,
            "target_discriminative_min": self.target_discriminative_min,
            "target_discriminative_max": self.target_discriminative_max,
            "total_items": self.total_items,
            "timeout_seconds": self.timeout_seconds,
            "scratch_dir": self.scratch_dir,
        }


def load_config(**overrides) -> BBAConfig:
    """Loads configuration with optional keyword overrides."""
    config = BBAConfig()
    for k, v in overrides.items():
        if hasattr(config, k):
            setattr(config, k, v)
    return config

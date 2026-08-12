"""Build the reproducible identity of the public evaluator."""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path
from typing import Any, Dict

from bba.evidence import file_digest
from bba.protocol import digest_json


BOUND_MODULES = (
    "audit.py",
    "catalog.py",
    "damage.py",
    "dependencies.py",
    "protocol.py",
    "replay.py",
    "runtime.py",
    "scoring.py",
    "tournament.py",
    "validator.py",
)
BOUND_DISTRIBUTIONS = (
    "anthropic",
    "cryptography",
    "google-adk",
    "google-genai",
    "litellm",
)


def build_evaluator_identity(
    creator_prompt_digest: str,
    solver_prompt_digest: str,
    catalog_digest: str,
) -> Dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    repository_root = package_root.parent
    components = {
        f"bba/{name}": file_digest(package_root / name) for name in BOUND_MODULES
    }
    components["pyproject.toml"] = file_digest(repository_root / "pyproject.toml")
    components["dependency-wheel-catalog"] = file_digest(
        package_root / "data" / "dependency-wheels" / "catalog.json"
    )
    components["price-catalog"] = file_digest(package_root / "data" / "price-catalog.json")
    runtime = {
        "python": sys.version,
        "distributions": {
            name: importlib.metadata.version(name) for name in BOUND_DISTRIBUTIONS
        },
    }
    value = {
        "schema_version": 1,
        "components": components,
        "creator_prompt_digest": creator_prompt_digest,
        "solver_prompt_digest": solver_prompt_digest,
        "catalog_digest": catalog_digest,
        "runtime": runtime,
    }
    value["root_digest"] = digest_json(value)
    return value

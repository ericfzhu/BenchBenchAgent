"""Build the reproducible identity of the public evaluator."""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path
from typing import Any, Dict

from bba.evidence import file_digest
from bba.protocol import digest_json


BOUND_MODULES = (
    "adk_runtime.py",
    "audit.py",
    "audit_runner.py",
    "budget.py",
    "catalog.py",
    "damage.py",
    "dependencies.py",
    "_evidence.py",
    "evidence.py",
    "evaluator_identity.py",
    "holdouts.py",
    "protocol.py",
    "registry.py",
    "replay.py",
    "runtime.py",
    "scoring.py",
    "state.py",
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


def verify_evaluator_identity(manifest: Any) -> None:
    """Reject a frozen epoch when the local evaluator no longer matches it."""

    frozen = manifest.evaluator_components
    from bba.catalog import CATALOG_DIGEST

    current = build_evaluator_identity(
        manifest.creator_prompt_digest,
        manifest.solver_prompt_digest,
        CATALOG_DIGEST,
    )
    if current != frozen or current["root_digest"] != manifest.evaluator_version:
        raise ValueError("local evaluator does not match the frozen epoch identity")

"""Create BBA epochs from BBA-owned protocol data."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from bba.adk_runtime import CREATOR_INSTRUCTION, SOLVER_INSTRUCTION
from bba.catalog import CATALOG_VERSION, GCP_LOCATION, SERVERLESS_COHORT
from bba.protocol import ExperimentManifest, digest_json, to_primitive


def new_epoch_id() -> str:
    """Create a readable, collision-resistant local epoch ID."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"epoch-{timestamp}-{secrets.token_hex(3)}"


def create_hidden_epoch_material(epoch_id: str) -> dict[str, Any]:
    """Create private inputs before any creator run."""

    return {
        "schema_version": 1,
        "epoch_id": epoch_id,
        "hidden_solver_panel": {
            "catalog_version": CATALOG_VERSION,
            "models": to_primitive(SERVERLESS_COHORT),
            "scaffold_seed": secrets.randbits(63),
        },
        "hidden_seeds": {
            "generator_seeds": [secrets.randbits(63) for _ in range(3)],
            "solver_seeds": [secrets.randbits(63) for _ in range(3)],
        },
        "audit_policy": {
            "version": "bba-bbb-audit-v1",
            "decision_level_metrics": True,
            "disclose_only_after_close": True,
            "nonce": secrets.token_hex(32),
        },
    }


def create_experiment_manifest(
    project: str,
    *,
    epoch_id: Optional[str] = None,
) -> tuple[ExperimentManifest, dict[str, Any]]:
    """Create a complete manifest without operator-supplied protocol settings."""

    identifier = epoch_id or new_epoch_id()
    hidden = create_hidden_epoch_material(identifier)
    commitments = {
        name: digest_json(hidden[name])
        for name in ("hidden_solver_panel", "hidden_seeds", "audit_policy")
    }
    manifest = ExperimentManifest(
        epoch_id=identifier,
        cohort=SERVERLESS_COHORT,
        catalog_version=CATALOG_VERSION,
        gcp_project=project,
        gcp_location=GCP_LOCATION,
        public_seed=secrets.randbits(63),
        hidden_commitments=commitments,
        creator_prompt_digest=digest_json(CREATOR_INSTRUCTION),
        solver_prompt_digest=digest_json(SOLVER_INSTRUCTION),
        evaluator_version="bba-public-evaluator-v1",
    )
    return manifest, hidden

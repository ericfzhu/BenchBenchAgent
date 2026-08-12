"""Create BBA epochs from BBA-owned protocol data."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

from bba.adk_runtime import CREATOR_INSTRUCTION, SOLVER_INSTRUCTION
from bba.catalog import CATALOG_DIGEST, CATALOG_VERSION, GCP_LOCATION, SERVERLESS_COHORT
from bba.evaluator_identity import build_evaluator_identity
from bba.protocol import ExperimentManifest, digest_json, to_primitive


def new_epoch_id() -> str:
    """Create a readable, collision-resistant local epoch ID."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"epoch-{timestamp}-{secrets.token_hex(3)}"


def create_hidden_epoch_material(epoch_id: str) -> dict[str, Any]:
    """Create private inputs before any creator run."""

    scaffold_seed = secrets.randbits(63)
    hidden_models = tuple(
        replace(model, scaffold=f"sealed-v1-{scaffold_seed:x}")
        for model in SERVERLESS_COHORT
    )
    return {
        "hidden_solver_panel": {
            "epoch_id": epoch_id,
            "catalog_version": CATALOG_VERSION,
            "models": to_primitive(hidden_models),
            "scaffold_seed": scaffold_seed,
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
    creator_prompt_digest = digest_json(CREATOR_INSTRUCTION)
    solver_prompt_digest = digest_json(SOLVER_INSTRUCTION)
    evaluator = build_evaluator_identity(
        creator_prompt_digest, solver_prompt_digest, CATALOG_DIGEST
    )
    manifest = ExperimentManifest(
        epoch_id=identifier,
        cohort=SERVERLESS_COHORT,
        catalog_version=CATALOG_VERSION,
        gcp_project=project,
        gcp_location=GCP_LOCATION,
        hidden_commitments=commitments,
        creator_prompt_digest=creator_prompt_digest,
        solver_prompt_digest=solver_prompt_digest,
        evaluator_version=evaluator["root_digest"],
        evaluator_components=evaluator,
    )
    return manifest, hidden

"""Small paid Vertex AI readiness check for the frozen catalog."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

from bba.adk_runtime import AdkSolverBackend, build_adk_backends
from bba.evidence import EvidenceStore
from bba.observability import LocalObservabilityStore
from bba.protocol import ExperimentManifest, to_primitive
from bba.runtime import SecureSandbox


def run_preflight(
    manifest: ExperimentManifest,
    evidence: EvidenceStore,
    solver_backends: Mapping[str, AdkSolverBackend] | None = None,
) -> Dict[str, Any]:
    if manifest.gcp_location != "global":
        raise ValueError("BBA serverless catalog requires the global location")
    if solver_backends is None:
        sandbox = SecureSandbox(
            memory_mb=manifest.budget.memory_mb,
            process_limit=manifest.budget.process_limit,
            cpu_seconds=manifest.budget.cpu_seconds,
        )
        _creators, solver_backends = build_adk_backends(
            manifest,
            construction_sandbox=sandbox,
            observability_store=LocalObservabilityStore(evidence.root),
        )
    results = []
    for identity in manifest.cohort:
        backend = solver_backends[identity.artifact_id]
        with tempfile.TemporaryDirectory(prefix="bba-preflight-") as temporary:
            bundle = Path(temporary)
            (bundle / "solver_packet.md").write_text(
                "Return the answer value for the one declared item.", encoding="utf-8"
            )
            items = ({"id": "preflight-item", "answer_hint": 1},)
            predictions = backend.solve(identity, bundle, items, 0, manifest)
            debrief = backend.take_debrief()
            trace = backend.take_trace()
        if predictions != [{"id": "preflight-item", "answer": 1}]:
            raise RuntimeError(f"model did not complete the preflight tool contract: {identity.artifact_id}")
        if debrief is None or tuple(item.item_id for item in debrief.items) != ("preflight-item",):
            raise RuntimeError(f"model did not return a valid solver debrief: {identity.artifact_id}")
        if trace is None or not trace.usage_metadata_complete:
            raise RuntimeError(f"model did not return complete usage metadata: {identity.artifact_id}")
        if not {"submit_predictions", "submit_debrief"}.issubset(trace.tool_calls):
            raise RuntimeError(f"model did not complete both solver tool contracts: {identity.artifact_id}")
        if trace.identity != identity:
            raise RuntimeError(f"preflight response identity mismatch: {identity.artifact_id}")
        returned_versions = tuple(trace.response_model_versions)
        identity_check = "verified" if returned_versions else "provider_field_unavailable"
        results.append({
            "identity": identity.artifact_id,
            "route": identity.adk_model,
            "location": manifest.gcp_location,
            "behavior_settings": dict(identity.behavior_settings),
            "usage": {
                "prompt_tokens": trace.prompt_tokens,
                "output_tokens": trace.output_tokens,
                "total_tokens": trace.total_tokens,
            },
            "tool_contract_passed": True,
            "returned_model_versions": returned_versions,
            "response_identity_check": identity_check,
            "serverless": True,
        })
    record = {
        "schema_version": 1,
        "epoch_id": manifest.epoch_id,
        "manifest_digest": manifest.digest,
        "catalog_version": manifest.catalog_version,
        "gcp_project": manifest.gcp_project,
        "gcp_location": manifest.gcp_location,
        "deployment_created": False,
        "models": results,
        "passed": len(results) == len(manifest.cohort),
    }
    evidence.publish_record_idempotent(manifest.epoch_id, "preflight", "vertex", record)
    return record

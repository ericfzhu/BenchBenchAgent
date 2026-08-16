"""Small paid Vertex AI readiness check for the frozen catalog."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

from bba.adk_runtime import AdkSolverBackend, build_adk_solver_backends
from bba.evidence import EvidenceStore
from bba.observability import LocalObservabilityStore
from bba.protocol import ExperimentManifest, ModelIdentity
from bba.runtime import SecureSandbox
from bba.tracing import trace_span, traced


def _failure_result(
    identity: ModelIdentity,
    manifest: ExperimentManifest,
    error: Exception,
) -> Dict[str, Any]:
    return {
        "identity": identity.artifact_id,
        "route": identity.adk_model,
        "location": manifest.gcp_location,
        "behavior_settings": dict(identity.behavior_settings),
        "usage": None,
        "tool_contract_passed": False,
        "returned_model_versions": (),
        "response_identity_check": "not_verified",
        "serverless": True,
        "passed": False,
        "error_type": type(error).__name__,
        "error": str(error)[:2000],
    }


@traced(
    "bba.epoch.preflight",
    lambda manifest, evidence, solver_backends=None: {
        "bba.epoch.id": manifest.epoch_id,
        "bba.model.count": len(manifest.cohort),
    },
)
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
        if not sandbox.available:
            reason = sandbox.unavailable_reason or "unknown sandbox failure"
            raise RuntimeError(f"secure local sandbox is unavailable: {reason}")
        if sandbox.backend != manifest.sandbox.backend:
            raise RuntimeError(
                "local sandbox backend does not match the frozen epoch manifest: "
                f"{sandbox.backend!r} != {manifest.sandbox.backend!r}"
            )
        solver_backends = build_adk_solver_backends(
            manifest,
            observability_store=LocalObservabilityStore(evidence.root),
        )

    results = []
    for identity in manifest.cohort:
        try:
            backend = solver_backends[identity.artifact_id]
            with tempfile.TemporaryDirectory(prefix="bba-preflight-") as temporary:
                bundle = Path(temporary)
                (bundle / "solver_packet.md").write_text(
                    "Return the answer value for the one declared item.",
                    encoding="utf-8",
                )
                items = ({"id": "preflight-item", "answer_hint": 1},)
                with trace_span(
                    "bba.preflight.model",
                    {
                        "bba.epoch.id": manifest.epoch_id,
                        "bba.model.identity": identity.artifact_id,
                        "bba.model.publisher": identity.publisher,
                    },
                ):
                    predictions = backend.solve(identity, bundle, items, 0, manifest)
                debrief = backend.take_debrief()
                trace = backend.take_trace()

            if predictions != [{"id": "preflight-item", "answer": 1}]:
                raise RuntimeError("model did not complete the preflight prediction contract")
            if debrief is None or tuple(item.item_id for item in debrief.items) != (
                "preflight-item",
            ):
                raise RuntimeError("model did not return a valid solver debrief")
            if trace is None or not trace.usage_metadata_complete:
                raise RuntimeError("model did not return complete usage metadata")
            if not {"submit_predictions", "submit_debrief"}.issubset(
                trace.tool_calls
            ):
                raise RuntimeError("model did not complete both solver tool contracts")
            if trace.identity != identity:
                raise RuntimeError("preflight response identity mismatch")

            returned_versions = tuple(trace.response_model_versions)
            identity_check = (
                "verified" if returned_versions else "provider_field_unavailable"
            )
            results.append(
                {
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
                    "passed": True,
                    "error_type": None,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(_failure_result(identity, manifest, exc))

    passed = len(results) == len(manifest.cohort) and all(
        bool(result["passed"]) for result in results
    )
    record = {
        "schema_version": 2,
        "epoch_id": manifest.epoch_id,
        "manifest_digest": manifest.digest,
        "catalog_version": manifest.catalog_version,
        "gcp_project": manifest.gcp_project,
        "gcp_location": manifest.gcp_location,
        "deployment_created": False,
        "models": results,
        "passed": passed,
    }
    if passed:
        evidence.publish_record_idempotent(
            manifest.epoch_id, "preflight", "vertex", record
        )
    else:
        evidence.publish_attempt_record(
            manifest.epoch_id, "preflight-attempts", "vertex", record
        )
    return record

"""Redacted, local operational telemetry for BBA ADK invocations."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bba.protocol import canonical_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalObservabilityStore:
    """Save one replaceable status record for each local ADK invocation.

    These files are an operator view. Immutable invocation evidence remains in
    ``agent-traces``. The records contain identifiers, counts, timings, and
    error types. They do not contain messages, prompts, tool arguments, tool
    results, model output, predictions, debrief text, or private audit data.
    """

    def __init__(self, evidence_root: Path) -> None:
        self.evidence_root = Path(evidence_root).resolve()

    def _invocation_root(self, epoch_id: str) -> Path:
        if not epoch_id or "/" in epoch_id or "\\" in epoch_id:
            raise ValueError("invalid observability epoch ID")
        return self.evidence_root / "epochs" / epoch_id / "observability" / "invocations"

    def update(self, record: Mapping[str, Any]) -> Path:
        """Atomically replace the current status for one invocation."""

        epoch_id = str(record.get("epoch_id", ""))
        observation_id = str(record.get("observation_id", ""))
        if not observation_id or not observation_id.isalnum():
            raise ValueError("invalid observability invocation ID")
        root = self._invocation_root(epoch_id)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"{observation_id}.json"
        value = dict(record)
        value["updated_at"] = _utc_now()
        data = canonical_json(value) + b"\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{observation_id}.", dir=str(root)
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return destination

    def invocations(self, epoch_id: str) -> list[dict[str, Any]]:
        """Return readable invocation records in start-time order."""

        root = self._invocation_root(epoch_id)
        records = []
        for path in root.glob("*.json"):
            try:
                import json

                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("epoch_id") == epoch_id:
                    records.append(value)
            except (OSError, ValueError):
                continue
        return sorted(
            records,
            key=lambda item: (str(item.get("started_at", "")), str(item.get("observation_id", ""))),
        )

    def recover_interrupted(self, epoch_id: str) -> int:
        """Close stale running records after the prior controller stopped."""

        recovered = 0
        for record in self.invocations(epoch_id):
            if record.get("status") != "running":
                continue
            record["status"] = "interrupted"
            record["error_type"] = "ProcessInterrupted"
            record["usage_metadata_complete"] = False
            self.update(record)
            recovered += 1
        return recovered

    def summary(self, epoch_id: str, recent_limit: int = 20) -> dict[str, Any]:
        """Aggregate local ADK health, use, and latency for one epoch."""

        if recent_limit < 1:
            raise ValueError("recent observability limit must be positive")
        records = self.invocations(epoch_id)
        statuses = Counter(str(item.get("status", "unknown")) for item in records)
        totals = {
            "invocations": len(records),
            "model_calls": sum(int(item.get("model_calls", 0)) for item in records),
            "tool_calls": sum(int(item.get("tool_call_count", 0)) for item in records),
            "prompt_tokens": sum(int(item.get("prompt_tokens", 0)) for item in records),
            "output_tokens": sum(int(item.get("output_tokens", 0)) for item in records),
            "total_tokens": sum(int(item.get("total_tokens", 0)) for item in records),
            "duration_ms": round(
                sum(float(item.get("duration_ms", 0.0)) for item in records), 3
            ),
        }

        groups: dict[str, dict[str, Any]] = {}
        for record in records:
            identity = record.get("identity") or {}
            identity_id = str(identity.get("artifact_id", "unknown"))
            row = groups.setdefault(identity_id, {
                "identity": identity_id,
                "publisher": identity.get("publisher"),
                "model": identity.get("model"),
                "invocations": 0,
                "failures": 0,
                "model_calls": 0,
                "tool_calls": 0,
                "total_tokens": 0,
                "duration_ms": 0.0,
            })
            row["invocations"] += 1
            row["failures"] += int(
                record.get("status")
                in {"timeout", "provider_error", "failed", "interrupted"}
            )
            row["model_calls"] += int(record.get("model_calls", 0))
            row["tool_calls"] += int(record.get("tool_call_count", 0))
            row["total_tokens"] += int(record.get("total_tokens", 0))
            row["duration_ms"] += float(record.get("duration_ms", 0.0))
        for row in groups.values():
            row["duration_ms"] = round(row["duration_ms"], 3)

        recent = []
        for record in reversed(records[-recent_limit:]):
            identity = record.get("identity") or {}
            recent.append({
                "observation_id": record.get("observation_id"),
                "role": record.get("role"),
                "identity": identity.get("artifact_id", "unknown"),
                "status": record.get("status", "unknown"),
                "model_calls": int(record.get("model_calls", 0)),
                "tool_calls": int(record.get("tool_call_count", 0)),
                "total_tokens": int(record.get("total_tokens", 0)),
                "duration_ms": float(record.get("duration_ms", 0.0)),
                "error_type": record.get("error_type"),
                "started_at": record.get("started_at"),
                "updated_at": record.get("updated_at"),
            })

        return {
            "schema_version": 1,
            "epoch_id": epoch_id,
            "generated_at": _utc_now(),
            "status_counts": dict(sorted(statuses.items())),
            "active": statuses.get("running", 0),
            "failures": sum(
                count
                for status, count in statuses.items()
                if status in {"timeout", "provider_error", "failed", "interrupted"}
            ),
            "usage_metadata_complete": all(
                bool(item.get("usage_metadata_complete"))
                for item in records
                if item.get("status") != "running"
            ),
            "totals": totals,
            "models": sorted(groups.values(), key=lambda item: item["identity"]),
            "recent": recent,
        }

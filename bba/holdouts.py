"""Cross-epoch append-only holdout lifecycle registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from bba.evidence import EvidenceStore, file_digest, read_json
from bba.protocol import canonical_json, digest_json
from bba.state import local_file_lock


class HoldoutRegistry:
    def __init__(self, evidence: EvidenceStore):
        self.evidence = evidence
        self.name = "holdouts"

    @staticmethod
    def commitment_id(commitments: Mapping[str, str]) -> str:
        return digest_json(dict(commitments))

    def _records(self) -> list[Dict[str, Any]]:
        root = self.evidence.root / "registries" / self.name
        previous = None
        records = []
        for path in sorted(root.glob("*.json")):
            body = read_json(path)
            if body.get("previous_record_digest") != previous:
                raise ValueError("holdout registry hash chain is invalid")
            previous = file_digest(path)
            records.append(body["record"])
        return records

    def state(self, commitment_id: str) -> str | None:
        states = [
            item["state"] for item in self._records()
            if item.get("commitment_id") == commitment_id
        ]
        return states[-1] if states else None

    def transition(
        self,
        epoch_id: str,
        commitments: Mapping[str, str],
        state: str,
    ) -> Path:
        if state not in {"committed", "opened", "retired"}:
            raise ValueError("unknown holdout state")
        commitment_id = self.commitment_id(commitments)
        with local_file_lock(self.evidence.root, "registry-holdouts"):
            current = self.state(commitment_id)
            allowed = {
                None: "committed",
                "committed": "opened",
                "opened": "retired",
            }
            if current == state:
                record = next(
                    item for item in reversed(self._records())
                    if item.get("commitment_id") == commitment_id
                )
                if record["epoch_id"] != epoch_id:
                    raise ValueError("holdout commitment is already owned by another epoch")
                root = self.evidence.root / "registries" / self.name
                return next(
                    path for path in sorted(root.glob("*.json"))
                    if canonical_json(read_json(path)["record"]) == canonical_json(record)
                )
            if allowed.get(current) != state:
                if current == "retired":
                    raise ValueError("retired holdout material cannot be reused")
                raise ValueError(f"invalid holdout transition {current!r} to {state!r}")
            if state == "committed":
                for record in self._records():
                    if record.get("commitment_id") == commitment_id:
                        raise ValueError("holdout commitment was used by an earlier epoch")
            record = {
                "schema_version": 1,
                "epoch_id": epoch_id,
                "commitment_id": commitment_id,
                "commitments": dict(commitments),
                "state": state,
            }
            return self.evidence.append_registry_record(self.name, record)

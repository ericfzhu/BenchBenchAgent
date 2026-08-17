"""Public evidence API with contained epoch paths and review immutability guards."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from bba._evidence import *  # noqa: F401,F403
from bba._evidence import EvidenceStore as _EvidenceStore
from bba.protocol import SolvabilityCertificate


_EPOCH_ID_PATTERN = re.compile(r"[a-zA-Z0-9._-]{1,100}")


def validate_epoch_id(epoch_id: str) -> str:
    """Return one filesystem-safe epoch identifier, rejecting path components."""

    value = str(epoch_id).strip()
    if (
        value in {".", ".."}
        or _EPOCH_ID_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(
            "epoch ID must use 1 to 100 letters, numbers, dots, dashes, or "
            "underscores and cannot be '.' or '..'"
        )
    return value


class EvidenceStore(_EvidenceStore):
    """Evidence store with contained epoch paths and frozen review inputs."""

    def epoch_root(self, epoch_id: str) -> Path:
        """Return a direct child of ``epochs`` and reject symlink escapes."""

        value = validate_epoch_id(epoch_id)
        epochs_root = (self.root / "epochs").resolve()
        candidate = epochs_root / value
        resolved = candidate.resolve(strict=False)
        if resolved.parent != epochs_root:
            raise ValueError("epoch path must remain a direct child of the evidence root")
        return resolved

    def review_window_closed(self, epoch_id: str) -> bool:
        root = self.epoch_root(epoch_id)
        return (
            (root / "audit" / "public-population.json").is_file()
            or (root / "evaluation" / "public.json").is_file()
        )

    def freeze_solvability_certificate(
        self,
        epoch_id: str,
        certificate: SolvabilityCertificate,
        artifacts: Mapping[str, Path],
    ) -> Path:
        final_root = (
            self.epoch_root(epoch_id)
            / "solvability-certificates"
            / certificate.digest
        )
        if not final_root.exists() and self.review_window_closed(epoch_id):
            raise RuntimeError(
                "the review window closed when the public audit population was frozen"
            )
        return super().freeze_solvability_certificate(
            epoch_id, certificate, artifacts
        )

    def publish_record_idempotent(
        self,
        epoch_id: str,
        category: str,
        record_id: str,
        value: Any,
    ) -> Path:
        destination = self.record_path(epoch_id, category, record_id)
        if (
            category == "promotions"
            and not destination.exists()
            and self.review_window_closed(epoch_id)
        ):
            raise RuntimeError(
                "the review window closed when the public audit population was frozen"
            )
        return super().publish_record_idempotent(
            epoch_id, category, record_id, value
        )

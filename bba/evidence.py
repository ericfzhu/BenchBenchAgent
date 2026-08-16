"""Public evidence API with phase-bound review immutability guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bba._evidence import *  # noqa: F401,F403
from bba._evidence import EvidenceStore as _EvidenceStore
from bba.protocol import SolvabilityCertificate


class EvidenceStore(_EvidenceStore):
    """Evidence store that closes human-input surfaces before audit freezing.

    Existing immutable records remain idempotently replayable after the review
    window closes. New certificates and promotion records are rejected once the
    public audit population has been frozen, and therefore cannot alter the
    public or sealed evaluation after its inputs have been committed.
    """

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

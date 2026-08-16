"""Review-window immutability tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bba.evidence import EvidenceStore, file_digest
from bba.protocol import SolvabilityCertificate, SolvabilityCertificateType


class TestReviewWindow(unittest.TestCase):
    def test_new_human_evidence_is_rejected_after_audit_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = EvidenceStore(Path(temporary))
            epoch_id = "review-window-test"
            artifact = Path(temporary) / "review-notes.txt"
            artifact.write_text("independent evidence", encoding="utf-8")
            certificate = SolvabilityCertificate(
                snapshot_id="snapshot-one",
                design_digest="a" * 64,
                instance_digest="b" * 64,
                certificate_type=SolvabilityCertificateType.INDEPENDENT_REFERENCE,
                issuer_id="independent-reviewer",
                independence_basis="The issuer did not create the benchmark.",
                verification_method="Checked the public solver material.",
                scope="All frozen items.",
                evidence_digests={"notes.txt": file_digest(artifact)},
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            evidence.freeze_solvability_certificate(
                epoch_id, certificate, {"notes.txt": artifact}
            )
            evidence.publish_record_idempotent(
                epoch_id,
                "promotions",
                "existing-review",
                {"decision": "approved"},
            )
            evidence.publish_record_idempotent(
                epoch_id,
                "audit",
                "public-population",
                {"schema_version": 1},
            )

            # Exact retries remain idempotent after the boundary.
            evidence.freeze_solvability_certificate(
                epoch_id, certificate, {"notes.txt": artifact}
            )
            evidence.publish_record_idempotent(
                epoch_id,
                "promotions",
                "existing-review",
                {"decision": "approved"},
            )

            later = SolvabilityCertificate(
                snapshot_id=certificate.snapshot_id,
                design_digest=certificate.design_digest,
                instance_digest=certificate.instance_digest,
                certificate_type=certificate.certificate_type,
                issuer_id="second-reviewer",
                independence_basis=certificate.independence_basis,
                verification_method=certificate.verification_method,
                scope=certificate.scope,
                evidence_digests=certificate.evidence_digests,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            with self.assertRaisesRegex(RuntimeError, "review window closed"):
                evidence.freeze_solvability_certificate(
                    epoch_id, later, {"notes.txt": artifact}
                )
            with self.assertRaisesRegex(RuntimeError, "review window closed"):
                evidence.publish_record_idempotent(
                    epoch_id,
                    "promotions",
                    "late-review",
                    {"decision": "approved"},
                )


if __name__ == "__main__":
    unittest.main()

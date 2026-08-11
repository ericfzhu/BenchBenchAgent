"""Human-signed canonical promotion and append-only registries."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping

from bba.evidence import EvidenceStore
from bba.protocol import PromotionDecision, PromotionRecord, canonical_json


class PromotionRegistry:
    def __init__(self, evidence: EvidenceStore, registry_name: str = "canonical-benchmarks"):
        self.evidence = evidence
        self.registry_name = registry_name

    @staticmethod
    def sign(record: PromotionRecord, signing_key: bytes) -> PromotionRecord:
        if not signing_key:
            raise ValueError("a non-empty reviewer signing key is required")
        signature = hmac.new(signing_key, canonical_json(record.unsigned_payload()), hashlib.sha256).hexdigest()
        return replace(record, signature=signature)

    @staticmethod
    def verify(record: PromotionRecord, signing_key: bytes) -> bool:
        if not record.signature or not signing_key:
            return False
        expected = hmac.new(signing_key, canonical_json(record.unsigned_payload()), hashlib.sha256).hexdigest()
        return hmac.compare_digest(record.signature, expected)

    def append(self, record: PromotionRecord, signing_key: bytes) -> Path:
        if not self.verify(record, signing_key):
            raise ValueError("promotion signature is invalid")
        if record.decision == PromotionDecision.APPROVED and len(record.sampled_item_ids) != 6:
            raise ValueError("approved promotions require six independently reconstructed items")
        if len(set(record.sampled_item_ids)) != len(record.sampled_item_ids):
            raise ValueError("review samples must be unique")
        if not record.reviewer_id or not record.key_id or not record.reconstructed_answers_digest:
            raise ValueError("promotion attestation is incomplete")
        return self.evidence.append_registry_record(self.registry_name, record)


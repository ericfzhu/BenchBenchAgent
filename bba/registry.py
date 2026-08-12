"""Human-signed canonical promotion and append-only registries."""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from bba.evidence import EvidenceStore, read_json
from bba.protocol import (
    PromotionDecision,
    PromotionRecord,
    canonical_json,
    promotion_record_from_mapping,
)
from bba.state import local_file_lock


class PromotionRegistry:
    def __init__(self, evidence: EvidenceStore, registry_name: str = "canonical-benchmarks"):
        self.evidence = evidence
        self.registry_name = registry_name

    def trust_key(self, key_id: str, public_key: bytes) -> Path:
        key = self._load_public_key(public_key)
        raw = key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        record = {
            "schema_version": 1,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "public_key": base64.b64encode(raw).decode("ascii"),
        }
        with local_file_lock(self.evidence.root, "registry-reviewer-trust"):
            existing = self._trusted_key_record(key_id)
            if existing is not None:
                if canonical_json(existing[1]) != canonical_json(record):
                    raise ValueError(f"reviewer key ID has different trusted key: {key_id}")
                return existing[0]
            return self.evidence.append_registry_record("reviewer-trust", record)

    @staticmethod
    def _load_private_key(value: bytes) -> Ed25519PrivateKey:
        try:
            key = serialization.load_pem_private_key(value, password=None)
        except ValueError:
            if len(value) != 32:
                raise ValueError("reviewer key must be Ed25519 PEM or 32 raw bytes")
            key = Ed25519PrivateKey.from_private_bytes(value)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("reviewer private key is not Ed25519")
        return key

    @staticmethod
    def _load_public_key(value: bytes) -> Ed25519PublicKey:
        try:
            key = serialization.load_pem_public_key(value)
        except ValueError:
            if len(value) != 32:
                raise ValueError("reviewer public key must be Ed25519 PEM or 32 raw bytes")
            key = Ed25519PublicKey.from_public_bytes(value)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("reviewer public key is not Ed25519")
        return key

    @classmethod
    def sign(cls, record: PromotionRecord, signing_key: bytes) -> PromotionRecord:
        private_key = cls._load_private_key(signing_key)
        signature = private_key.sign(canonical_json(record.unsigned_payload()))
        return replace(record, signature=base64.b64encode(signature).decode("ascii"))

    def verify(self, record: PromotionRecord) -> bool:
        if not record.signature:
            return False
        trusted = self._trusted_key_record(record.key_id)
        if trusted is None:
            return False
        raw = base64.b64decode(trusted[1]["public_key"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
        try:
            key.verify(
                base64.b64decode(record.signature, validate=True),
                canonical_json(record.unsigned_payload()),
            )
        except (InvalidSignature, ValueError):
            return False
        return True

    def append(self, record: PromotionRecord) -> Path:
        if not self.verify(record):
            raise ValueError("promotion signature is invalid")
        if record.decision == PromotionDecision.APPROVED and len(record.sampled_item_ids) != 6:
            raise ValueError("approved promotions require six independently reconstructed items")
        if len(set(record.sampled_item_ids)) != len(record.sampled_item_ids):
            raise ValueError("review samples must be unique")
        if not record.reviewer_id or not record.key_id or not record.reconstructed_answers_digest:
            raise ValueError("promotion attestation is incomplete")
        with local_file_lock(self.evidence.root, f"registry-{self.registry_name}"):
            existing = self.find_exact(record)
            if existing is not None:
                return existing
            return self.evidence.append_registry_record(self.registry_name, record)

    def _trusted_key_record(self, key_id: str) -> tuple[Path, dict] | None:
        registry = self.evidence.root / "registries" / "reviewer-trust"
        for path in sorted(registry.glob("*.json")):
            record = read_json(path)["record"]
            if record.get("key_id") == key_id:
                return path, record
        return None

    def find_exact(self, expected: PromotionRecord) -> Path | None:
        registry = self.evidence.root / "registries" / self.registry_name
        for path in sorted(registry.glob("*.json")):
            body = read_json(path)
            record = promotion_record_from_mapping(body["record"])
            if canonical_json(record) == canonical_json(expected):
                return path
        return None

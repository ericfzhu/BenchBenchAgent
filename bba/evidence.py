"""Content-addressed and append-only evidence storage."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from bba.protocol import CandidateSnapshot, ExperimentManifest, ModelIdentity, canonical_json, to_primitive


IGNORED_NAMES = {"__pycache__", ".DS_Store", ".pytest_cache"}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"artifact root is not a real directory: {root}")
    digest = hashlib.sha256()
    paths = []
    for path in root.rglob("*"):
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"artifact contains a link: {path.relative_to(root)}")
        if path.is_file():
            paths.append(path)
        elif not path.is_dir():
            raise ValueError(f"artifact contains a special file: {path.relative_to(root)}")
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def atomic_publish_json(path: Path, value: Any) -> None:
    """Publish JSON exactly once; concurrent or repeated writers fail."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(str(temp_path), str(path))
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to overwrite immutable evidence: {path}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def epoch_root(self, epoch_id: str) -> Path:
        return self.root / "epochs" / epoch_id

    def freeze_manifest(self, manifest: ExperimentManifest) -> Path:
        destination = self.epoch_root(manifest.epoch_id) / "manifest.json"
        atomic_publish_json(destination, manifest)
        return destination

    def freeze_candidate(
        self,
        epoch_id: str,
        source: Path,
        creator: ModelIdentity,
        round_index: int,
        parent_snapshot_id: Optional[str] = None,
    ) -> CandidateSnapshot:
        source = Path(source).resolve()
        package_digest = tree_digest(source)
        snapshot_id = f"{creator.artifact_id}.r{round_index}.{package_digest[:12]}"
        destination = self.epoch_root(epoch_id) / "candidates" / snapshot_id / "package"
        metadata_path = destination.parent / "snapshot.json"
        if destination.exists() or metadata_path.exists():
            raise FileExistsError(f"candidate snapshot already exists: {snapshot_id}")
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source, destination, symlinks=False)
        if tree_digest(destination) != package_digest:
            raise RuntimeError("candidate digest changed while freezing snapshot")
        snapshot = CandidateSnapshot(
            snapshot_id=snapshot_id,
            package_digest=package_digest,
            creator=creator,
            round_index=round_index,
            parent_snapshot_id=parent_snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            package_path=str(destination),
        )
        atomic_publish_json(metadata_path, snapshot)
        return snapshot

    def publish_record(self, epoch_id: str, category: str, record_id: str, value: Any) -> Path:
        if not category or not record_id or "/" in category or "/" in record_id:
            raise ValueError("invalid evidence record path")
        destination = self.epoch_root(epoch_id) / category / f"{record_id}.json"
        atomic_publish_json(destination, value)
        return destination

    def append_registry_record(self, registry_name: str, value: Any) -> Path:
        """Append a hash-chained record using one immutable JSON file per entry."""

        registry = self.root / "registries" / registry_name
        registry.mkdir(parents=True, exist_ok=True)
        existing = sorted(registry.glob("*.json"))
        previous = file_digest(existing[-1]) if existing else None
        body = {"previous_record_digest": previous, "record": to_primitive(value)}
        record_digest = hashlib.sha256(canonical_json(body)).hexdigest()
        sequence = len(existing) + 1
        destination = registry / f"{sequence:08d}-{record_digest[:12]}.json"
        atomic_publish_json(destination, body)
        return destination


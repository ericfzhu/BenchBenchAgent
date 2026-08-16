"""Content-addressed and append-only evidence storage."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from bba.protocol import (
    CandidateSnapshot,
    EvaluationInstance,
    ExperimentManifest,
    ModelIdentity,
    SolvabilityCertificate,
    SolverAttempt,
    candidate_snapshot_from_mapping,
    canonical_json,
    experiment_manifest_from_mapping,
    evaluation_instance_from_mapping,
    solver_attempt_from_mapping,
    solvability_certificate_from_mapping,
    to_primitive,
)


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


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def epoch_root(self, epoch_id: str) -> Path:
        return self.root / "epochs" / epoch_id

    def record_path(self, epoch_id: str, category: str, record_id: str) -> Path:
        if not category or not record_id or "/" in category or "/" in record_id:
            raise ValueError("invalid evidence record path")
        return self.epoch_root(epoch_id) / category / f"{record_id}.json"

    def freeze_manifest(self, manifest: ExperimentManifest) -> Path:
        destination = self.epoch_root(manifest.epoch_id) / "manifest.json"
        if destination.exists():
            frozen = experiment_manifest_from_mapping(read_json(destination))
            if frozen.digest != manifest.digest:
                raise ValueError("frozen epoch manifest does not match the requested manifest")
            return destination
        atomic_publish_json(destination, manifest)
        return destination

    def freeze_epoch_setup(self, manifest: ExperimentManifest, private: Any) -> Path:
        """Atomically freeze the public manifest and sealed controller inputs."""

        final_root = self.epoch_root(manifest.epoch_id)
        manifest_path = final_root / "manifest.json"
        private_path = final_root / "private" / "holdout-plan.json"
        if final_root.exists():
            if not manifest_path.is_file() or not private_path.is_file():
                raise RuntimeError(f"epoch setup is incomplete: {manifest.epoch_id}")
            frozen = experiment_manifest_from_mapping(read_json(manifest_path))
            if frozen.digest != manifest.digest:
                raise ValueError("frozen epoch manifest conflicts with requested setup")
            if canonical_json(read_json(private_path)) != canonical_json(private):
                raise ValueError("private epoch material conflicts with requested setup")
            return manifest_path

        epochs_root = final_root.parent
        epochs_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".epoch-setup-", dir=str(epochs_root))
        )
        try:
            atomic_publish_json(temporary_root / "manifest.json", manifest)
            atomic_publish_json(
                temporary_root / "private" / "holdout-plan.json",
                private,
            )
            os.rename(temporary_root, final_root)
            return manifest_path
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    def load_manifest(self, epoch_id: str) -> ExperimentManifest:
        path = self.epoch_root(epoch_id) / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"epoch manifest does not exist: {epoch_id}")
        return experiment_manifest_from_mapping(read_json(path))

    def load_snapshots(self, epoch_id: str) -> list[CandidateSnapshot]:
        root = self.epoch_root(epoch_id) / "candidates"
        snapshots = []
        for metadata_path in sorted(root.glob("*/snapshot.json")):
            if metadata_path.parent.name.startswith("."):
                continue
            snapshot = candidate_snapshot_from_mapping(read_json(metadata_path))
            design_path = metadata_path.parent / "design"
            if tree_digest(design_path) != snapshot.design_digest:
                raise ValueError(f"candidate snapshot digest is invalid: {snapshot.snapshot_id}")
            snapshots.append(dataclasses.replace(snapshot, design_path=str(design_path)))
        return snapshots

    def load_instances(self, epoch_id: str) -> list[EvaluationInstance]:
        root = self.epoch_root(epoch_id) / "instances"
        return self._load_instances_from(root)

    def load_hidden_instances(self, epoch_id: str) -> list[EvaluationInstance]:
        root = self.epoch_root(epoch_id) / "audit" / "hidden-instances"
        return self._load_instances_from(root)

    def _load_instances_from(self, root: Path) -> list[EvaluationInstance]:
        instances = []
        for metadata_path in sorted(root.glob("*/instance.json")):
            if metadata_path.parent.name.startswith("."):
                continue
            instance = evaluation_instance_from_mapping(read_json(metadata_path))
            instance_path = metadata_path.parent / "data"
            if tree_digest(instance_path) != instance.instance_digest:
                raise ValueError(f"evaluation instance digest is invalid: {instance.instance_id}")
            instances.append(dataclasses.replace(instance, instance_path=str(instance_path)))
        return instances

    def load_solver_attempts(self, epoch_id: str) -> list[SolverAttempt]:
        root = self.epoch_root(epoch_id) / "solver-attempts"
        attempts = []
        for metadata_path in sorted(root.glob("*/attempt.json")):
            if metadata_path.parent.name.startswith("."):
                continue
            attempt = solver_attempt_from_mapping(read_json(metadata_path))
            if metadata_path.parent.name != attempt.attempt_id:
                raise ValueError(f"solver attempt path has the wrong identity: {metadata_path}")
            for name, relative in attempt.evidence_files.items():
                artifact = (self.root / relative).resolve()
                try:
                    artifact.relative_to(metadata_path.parent.resolve())
                except ValueError as exc:
                    raise ValueError(f"solver attempt artifact escapes its evidence root: {name}") from exc
                if not artifact.is_file() or file_digest(artifact) != attempt.evidence_digests[name]:
                    raise ValueError(f"solver attempt artifact digest is invalid: {attempt.attempt_id}/{name}")
            attempts.append(attempt)
        return attempts

    def load_solvability_certificates(
        self, epoch_id: str
    ) -> list[SolvabilityCertificate]:
        root = self.epoch_root(epoch_id) / "solvability-certificates"
        certificates = []
        for metadata_path in sorted(root.glob("*/certificate.json")):
            if metadata_path.parent.name.startswith("."):
                continue
            certificate = solvability_certificate_from_mapping(
                read_json(metadata_path)
            )
            if metadata_path.parent.name != certificate.digest:
                raise ValueError(
                    f"solvability certificate path has the wrong digest: {metadata_path}"
                )
            artifact_root = metadata_path.parent / "artifacts"
            for name, expected_digest in certificate.evidence_digests.items():
                artifact = artifact_root / name
                if not artifact.is_file() or file_digest(artifact) != expected_digest:
                    raise ValueError(
                        f"solvability certificate evidence is invalid: {certificate.digest}/{name}"
                    )
            certificates.append(certificate)
        return certificates

    def freeze_solvability_certificate(
        self,
        epoch_id: str,
        certificate: SolvabilityCertificate,
        artifacts: Mapping[str, Path],
    ) -> Path:
        if set(artifacts) != set(certificate.evidence_digests):
            raise ValueError("solvability certificate artifacts do not match its evidence")
        root = self.epoch_root(epoch_id) / "solvability-certificates"
        final_root = root / certificate.digest
        metadata_path = final_root / "certificate.json"
        if final_root.exists():
            existing = solvability_certificate_from_mapping(read_json(metadata_path))
            if canonical_json(existing) != canonical_json(certificate):
                raise FileExistsError("solvability certificate conflicts with evidence")
            self.load_solvability_certificates(epoch_id)
            return metadata_path
        root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".solvability-certificate-", dir=str(root))
        )
        try:
            artifact_root = temporary_root / "artifacts"
            for name, source_value in artifacts.items():
                if not re.fullmatch(r"[a-zA-Z0-9._-]+", name):
                    raise ValueError("solvability evidence names must be filesystem safe")
                source = Path(source_value).resolve()
                if not source.is_file() or file_digest(source) != certificate.evidence_digests[name]:
                    raise ValueError(f"solvability evidence changed before freeze: {name}")
                artifact_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, artifact_root / name)
            atomic_publish_json(temporary_root / "certificate.json", certificate)
            os.rename(temporary_root, final_root)
            return metadata_path
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    def freeze_solver_attempt(
        self,
        epoch_id: str,
        attempt: SolverAttempt,
        artifacts: Mapping[str, Path],
    ) -> Path:
        """Freeze one solver attempt and all replay artifacts as one directory."""

        if set(artifacts) != set(attempt.evidence_files):
            raise ValueError("solver attempt artifact sources do not match its contract")
        root = self.epoch_root(epoch_id) / "solver-attempts"
        final_root = root / attempt.attempt_id
        metadata_path = final_root / "attempt.json"
        if final_root.exists():
            existing = solver_attempt_from_mapping(read_json(metadata_path))
            if canonical_json(existing) != canonical_json(attempt):
                raise FileExistsError(f"solver attempt conflicts with evidence: {attempt.attempt_id}")
            self.load_solver_attempts(epoch_id)
            return metadata_path
        root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=".solver-attempt-", dir=str(root)))
        try:
            for name, source_value in artifacts.items():
                source = Path(source_value).resolve()
                relative = Path(attempt.evidence_files[name])
                expected = (self.root / relative).resolve()
                try:
                    artifact_relative = expected.relative_to(final_root.resolve())
                except ValueError as exc:
                    raise ValueError(f"solver attempt artifact path escapes evidence: {name}") from exc
                destination = temporary_root / artifact_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if file_digest(destination) != attempt.evidence_digests[name]:
                    raise RuntimeError(f"solver attempt artifact changed while freezing: {name}")
            atomic_publish_json(temporary_root / "attempt.json", attempt)
            os.rename(temporary_root, final_root)
            return metadata_path
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    def freeze_candidate(
        self,
        epoch_id: str,
        source: Path,
        creator: ModelIdentity,
        round_index: int,
        parent_snapshot_id: Optional[str] = None,
    ) -> CandidateSnapshot:
        source = Path(source).resolve()
        design_digest = tree_digest(source)
        snapshot_id = f"{creator.artifact_id}.r{round_index}.{design_digest[:12]}"
        candidates_root = self.epoch_root(epoch_id) / "candidates"
        final_root = candidates_root / snapshot_id
        destination = final_root / "design"
        metadata_path = final_root / "snapshot.json"
        if metadata_path.is_file() and destination.is_dir():
            existing = candidate_snapshot_from_mapping(read_json(metadata_path))
            if (
                existing.design_digest != design_digest
                or existing.creator != creator
                or existing.round_index != round_index
                or existing.parent_snapshot_id != parent_snapshot_id
                or tree_digest(destination) != design_digest
            ):
                raise FileExistsError(f"candidate snapshot conflicts with evidence: {snapshot_id}")
            return dataclasses.replace(existing, design_path=str(destination))
        if final_root.exists():
            raise RuntimeError(f"candidate snapshot is incomplete: {snapshot_id}")

        candidates_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".candidate-freeze-", dir=str(candidates_root))
        )
        try:
            temporary_design = temporary_root / "design"
            shutil.copytree(source, temporary_design, symlinks=False)
            if tree_digest(temporary_design) != design_digest:
                raise RuntimeError("candidate digest changed while freezing snapshot")
            snapshot = CandidateSnapshot(
                snapshot_id=snapshot_id,
                design_digest=design_digest,
                creator=creator,
                round_index=round_index,
                parent_snapshot_id=parent_snapshot_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                design_path=str(destination),
            )
            atomic_publish_json(temporary_root / "snapshot.json", snapshot)
            try:
                os.rename(temporary_root, final_root)
            except FileExistsError:
                if metadata_path.is_file() and destination.is_dir():
                    existing = candidate_snapshot_from_mapping(read_json(metadata_path))
                    if (
                        existing.design_digest == design_digest
                        and existing.creator == creator
                        and existing.round_index == round_index
                        and existing.parent_snapshot_id == parent_snapshot_id
                        and tree_digest(destination) == design_digest
                    ):
                        return dataclasses.replace(existing, design_path=str(destination))
                raise
            return snapshot
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    def freeze_instance(
        self,
        epoch_id: str,
        source: Path,
        snapshot: CandidateSnapshot,
        seed: int,
        sample_count: int,
    ) -> EvaluationInstance:
        source = Path(source).resolve()
        instance_digest = tree_digest(source)
        instance_id = f"{snapshot.snapshot_id}.seed-{seed}.{instance_digest[:12]}"
        instances_root = self.epoch_root(epoch_id) / "instances"
        final_root = instances_root / instance_id
        destination = final_root / "data"
        metadata_path = final_root / "instance.json"
        expected = EvaluationInstance(
            instance_id=instance_id,
            snapshot_id=snapshot.snapshot_id,
            design_digest=snapshot.design_digest,
            instance_digest=instance_digest,
            round_index=snapshot.round_index,
            seed=seed,
            sample_count=sample_count,
            created_at="",
            instance_path=str(destination),
        )
        if metadata_path.is_file() and destination.is_dir():
            existing = evaluation_instance_from_mapping(read_json(metadata_path))
            comparable = dataclasses.replace(expected, created_at=existing.created_at)
            if existing != comparable or tree_digest(destination) != instance_digest:
                raise FileExistsError(f"evaluation instance conflicts with evidence: {instance_id}")
            return dataclasses.replace(existing, instance_path=str(destination))
        if final_root.exists():
            raise RuntimeError(f"evaluation instance is incomplete: {instance_id}")
        instances_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=".instance-freeze-", dir=str(instances_root)))
        try:
            temporary_data = temporary_root / "data"
            shutil.copytree(source, temporary_data, symlinks=False)
            if tree_digest(temporary_data) != instance_digest:
                raise RuntimeError("evaluation instance changed while freezing")
            instance = dataclasses.replace(
                expected,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            atomic_publish_json(temporary_root / "instance.json", instance)
            os.rename(temporary_root, final_root)
            return instance
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    def publish_record_idempotent(
        self,
        epoch_id: str,
        category: str,
        record_id: str,
        value: Any,
    ) -> Path:
        destination = self.record_path(epoch_id, category, record_id)
        if destination.exists():
            if canonical_json(read_json(destination)) != canonical_json(value):
                raise FileExistsError(f"immutable evidence conflicts with requested record: {destination}")
            return destination
        atomic_publish_json(destination, value)
        return destination

    def publish_attempt_record(
        self,
        epoch_id: str,
        category: str,
        record_id: str,
        value: Any,
    ) -> Path:
        attempt = 1
        while True:
            suffix = record_id if attempt == 1 else f"{record_id}--attempt-{attempt}"
            destination = self.record_path(epoch_id, category, suffix)
            if not destination.exists():
                atomic_publish_json(destination, value)
                return destination
            attempt += 1

    def read_record(self, epoch_id: str, category: str, record_id: str) -> Any:
        path = self.record_path(epoch_id, category, record_id)
        if not path.is_file():
            raise FileNotFoundError(f"evidence record does not exist: {path}")
        return read_json(path)

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

"""Read local BBA evidence and queue controlled epoch operations."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from bba.evidence import EvidenceStore, read_json
from bba.observability import LocalObservabilityStore
from bba.protocol import (
    CandidateStatus,
    PromotionDecision,
    ReviewFindings,
    SolvabilityCertificateType,
    to_primitive,
)
from bba.scoring import classify_candidate
from bba.state import LocalStateStore, local_file_lock
from bba.tournament import TournamentController
from bba.tracing import tracing_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OperatorJob:
    job_id: str
    label: str
    epoch_id: Optional[str]
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    output: str = ""
    error: str = ""


class OperatorJobQueue:
    """Run one local controller operation at a time."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bba-console")
        self._lock = threading.Lock()
        self._jobs: dict[str, OperatorJob] = {}
        self._closed = False

    def submit(
        self,
        label: str,
        epoch_id: Optional[str],
        operation: Callable[[], Any],
    ) -> OperatorJob:
        with self._lock:
            if self._closed:
                raise RuntimeError("the console operation queue is closed")
            if any(job.status in {"queued", "running"} for job in self._jobs.values()):
                raise RuntimeError("another console operation is queued or running")
            job = OperatorJob(uuid.uuid4().hex, label, epoch_id, "queued", _now())
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id, operation)
        return job

    def _run(self, job_id: str, operation: Callable[[], Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
        try:
            result = operation()
            output = result if isinstance(result, str) else json.dumps(
                to_primitive(result), indent=2, sort_keys=True
            )
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = str(exc)[-8000:]
                job.finished_at = _now()
            return
        with self._lock:
            job.status = "succeeded"
            job.output = output[-32000:]
            job.finished_at = _now()

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return asdict(job) if job else None

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            values = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [asdict(job) for job in values[:limit]]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                if job.status == "queued":
                    job.status = "failed"
                    job.error = "the console stopped before this operation started"
                    job.finished_at = _now()
        self._executor.shutdown(wait=False, cancel_futures=True)


class OperatorConsole:
    """Provide the local operator view and the approved mutation surface."""

    EPOCH_ACTIONS = {
        "preflight": "Run paid preflight",
        "run": "Run or resume public epoch",
        "freeze-audit": "Freeze audit population",
        "close": "Close public epoch",
        "audit": "Run sealed audit",
    }

    def __init__(self, evidence_root: Path, jobs: Optional[OperatorJobQueue] = None):
        self.evidence = EvidenceStore(evidence_root)
        self.state = LocalStateStore(self.evidence.root / "bba-state.sqlite3")
        self.observability_store = LocalObservabilityStore(self.evidence.root)
        self.jobs = jobs or OperatorJobQueue()
        self._process_lock = threading.Lock()
        self._active_process: Optional[subprocess.Popen[bytes]] = None
        self._closed = False

    @staticmethod
    def validate_epoch_id(epoch_id: str) -> str:
        value = epoch_id.strip()
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", value):
            raise ValueError("epoch ID must use 1 to 100 letters, numbers, dots, dashes, or underscores")
        return value

    def _controller(self, epoch_id: str) -> TournamentController:
        epoch_id = self.validate_epoch_id(epoch_id)
        manifest = self.evidence.load_manifest(epoch_id)
        return TournamentController(manifest, self.evidence, state=self.state)

    def _status(self, epoch_id: str) -> dict[str, Any]:
        epoch_id = self.validate_epoch_id(epoch_id)
        root = self.evidence.epoch_root(epoch_id)
        result = self.state.status(epoch_id)
        result.update({
            "snapshots": len(list((root / "candidates").glob("*/snapshot.json"))),
            "instances": len(list((root / "instances").glob("*/instance.json"))),
            "validations": len(list((root / "validations").glob("*.json"))),
            "solver_cells": len(list((root / "solver-cells").glob("*.json"))),
            "solvability_certificates": len(
                list((root / "solvability-certificates").glob("*/certificate.json"))
            ),
            "promotions": len(list((root / "promotions").glob("*.json"))),
            "public_closed": (root / "evaluation" / "public.json").is_file(),
            "holdout_complete": (root / "audit" / "holdout.json").is_file(),
            "observability": self.observability_store.summary(epoch_id),
            "tracing": tracing_status(),
        })
        return result

    def list_epochs(self) -> list[dict[str, Any]]:
        epochs = []
        for path in sorted((self.evidence.root / "epochs").glob("*/manifest.json"), reverse=True):
            try:
                manifest = self.evidence.load_manifest(path.parent.name)
                status = self._status(manifest.epoch_id)
                status.update({
                    "created_at": getattr(manifest, "created_at", None) or status.get("updated_at", "—"),
                    "catalog_version": manifest.catalog_version,
                    "model_count": len(manifest.cohort),
                })
                epochs.append(status)
            except Exception as exc:
                epochs.append({"epoch_id": path.parent.name, "phase": "unreadable", "error": str(exc)})
        return epochs

    def epoch(self, epoch_id: str) -> dict[str, Any]:
        epoch_id = self.validate_epoch_id(epoch_id)
        manifest = self.evidence.load_manifest(epoch_id)
        value = self._status(epoch_id)
        value["manifest"] = {
            "catalog_version": manifest.catalog_version,
            "created_at": getattr(manifest, "created_at", None) or value.get("updated_at", "—"),
            "gcp_project": manifest.gcp_project,
            "gcp_location": manifest.gcp_location,
            "models": len(manifest.cohort),
            "rounds": manifest.thresholds.rounds,
            "solver_repetitions": manifest.thresholds.solver_repetitions,
        }
        value["candidates"] = self.candidates(epoch_id)
        value["approved"] = sum(item["reviewed"] for item in value["candidates"])
        return value

    def candidates(self, epoch_id: str) -> list[dict[str, Any]]:
        epoch_id = self.validate_epoch_id(epoch_id)
        controller = self._controller(epoch_id)
        rows = []
        for snapshot in reversed(controller.snapshots):
            validation = controller.validations.get(snapshot.snapshot_id)
            if validation is None:
                status = CandidateStatus.INCOMPLETE
                best = panel = None
            else:
                evaluation = classify_candidate(
                    snapshot,
                    validation,
                    controller.cells.get(snapshot.snapshot_id, ()),
                    controller.manifest.cohort,
                    controller.manifest.thresholds.solver_repetitions,
                    controller.manifest.thresholds.rejection_accuracy,
                    controller.promotions.get(snapshot.design_digest),
                )
                status = evaluation.status
                best = evaluation.best_solver_median
                panel = evaluation.panel_median
            rows.append({
                "snapshot_id": snapshot.snapshot_id,
                "creator": snapshot.creator.artifact_id,
                "model": snapshot.creator.model,
                "round": snapshot.round_index,
                "status": status.value,
                "validation_passed": validation.passed if validation else None,
                "solver_cells": len(controller.cells.get(snapshot.snapshot_id, ())),
                "best_solver_median": best,
                "panel_median": panel,
                "certificate_count": sum(
                    item.snapshot_id == snapshot.snapshot_id
                    for item in controller.solvability_certificates.values()
                ),
                "reviewed": snapshot.design_digest in controller.promotions,
            })
        return rows

    def candidate(self, epoch_id: str, snapshot_id: str) -> dict[str, Any]:
        epoch_id = self.validate_epoch_id(epoch_id)
        controller = self._controller(epoch_id)
        snapshot = controller.snapshot_by_id(snapshot_id)
        row = next(item for item in self.candidates(epoch_id) if item["snapshot_id"] == snapshot_id)
        certificates = [
            to_primitive(item)
            | {"digest": item.digest}
            for item in controller.solvability_certificates.values()
            if item.snapshot_id == snapshot_id
        ]
        promotions = []
        for path in sorted((self.evidence.epoch_root(epoch_id) / "promotions").glob(f"{snapshot.design_digest}*.json")):
            promotions.append(read_json(path))
        selected_items = []
        if snapshot_id in controller.instances:
            selected_items = controller.select_human_certificate_items(snapshot)
        return row | {
            "design_digest": snapshot.design_digest,
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "created_at": snapshot.created_at,
            "certificates": certificates,
            "promotions": promotions,
            "certificate_item_ids": selected_items,
            "final_round": snapshot.round_index == controller.manifest.thresholds.rounds - 1,
        }

    def results(self, epoch_id: str) -> dict[str, Any]:
        epoch_id = self.validate_epoch_id(epoch_id)
        root = self.evidence.epoch_root(epoch_id)
        public_path = root / "evaluation" / "public.json"
        audit_path = root / "audit" / "holdout.json"
        return {
            "public": read_json(public_path) if public_path.is_file() else None,
            "audit": read_json(audit_path) if audit_path.is_file() else None,
        }

    def observability(self, epoch_id: str) -> dict[str, Any]:
        epoch_id = self.validate_epoch_id(epoch_id)
        self.evidence.load_manifest(epoch_id)
        return self.observability_store.summary(epoch_id) | {
            "tracing": tracing_status()
        }

    def _run_cli(self, arguments: Sequence[str]) -> str:
        with tempfile.TemporaryFile() as output:
            with self._process_lock:
                if self._closed:
                    raise RuntimeError("the console stopped before the operation started")
                process = subprocess.Popen(
                    [sys.executable, "-m", "bba.cli", *arguments],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                self._active_process = process
            try:
                return_code = process.wait()
            finally:
                with self._process_lock:
                    if self._active_process is process:
                        self._active_process = None
            output.seek(0, 2)
            length = output.tell()
            output.seek(max(0, length - 32000))
            text = output.read().decode("utf-8", errors="replace").strip()
        if return_code:
            raise RuntimeError(text or f"BBA command stopped with status {return_code}")
        return text

    def close(self) -> None:
        """Stop active local work so BBA can resume it on the next run."""

        with self._process_lock:
            self._closed = True
            process = self._active_process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        self.jobs.close()

    def create_epoch(self, epoch_id: str) -> OperatorJob:
        epoch_id = self.validate_epoch_id(epoch_id)
        return self.jobs.submit(
            "Create epoch",
            epoch_id,
            lambda: self._run_cli([
                "epoch", "create", "--epoch-id", epoch_id,
                "--evidence-root", str(self.evidence.root),
            ]),
        )

    def delete_epoch(self, epoch_id: str) -> None:
        epoch_id = self.validate_epoch_id(epoch_id)
        import shutil
        from bba.state import local_file_lock
        with local_file_lock(self.evidence.root, f"epoch-{epoch_id}"):
            root = self.evidence.epoch_root(epoch_id)
            if root.exists():
                shutil.rmtree(root)
            self.state.delete_epoch(epoch_id)


    def run_epoch_action(self, epoch_id: str, action: str) -> OperatorJob:
        epoch_id = self.validate_epoch_id(epoch_id)
        if action not in self.EPOCH_ACTIONS:
            raise ValueError("unknown epoch action")
        self.evidence.load_manifest(epoch_id)
        return self.jobs.submit(
            self.EPOCH_ACTIONS[action],
            epoch_id,
            lambda: self._run_cli([
                "epoch", action, "--epoch-id", epoch_id,
                "--evidence-root", str(self.evidence.root),
            ]),
        )

    @staticmethod
    def _parse_evidence_lines(value: str) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for line in value.splitlines():
            if not line.strip():
                continue
            name, separator, path = line.strip().partition("=")
            if not separator or not name or not path or name in files:
                raise ValueError("each evidence line must be a unique NAME=/absolute/path")
            source = Path(path)
            if not source.is_absolute():
                raise ValueError("each solvability evidence path must be absolute")
            files[name] = source
        return files

    def record_certificate(
        self,
        epoch_id: str,
        snapshot_id: str,
        certificate_type: str,
        issuer_id: str,
        independence_basis: str,
        verification_method: str,
        scope: str,
        evidence_lines: str,
        answers_path: str,
    ) -> OperatorJob:
        epoch_id = self.validate_epoch_id(epoch_id)
        def operation() -> Any:
            answers = None
            if answers_path.strip():
                path = Path(answers_path.strip())
                if not path.is_absolute():
                    raise ValueError("the certificate answers path must be absolute")
                answers = read_json(path)
                if not isinstance(answers, dict):
                    raise ValueError("certificate answers must be one JSON object")
            with local_file_lock(self.evidence.root, f"epoch-{epoch_id}"):
                controller = self._controller(epoch_id)
                snapshot = controller.snapshot_by_id(snapshot_id)
                return controller.record_solvability_certificate(
                    snapshot,
                    SolvabilityCertificateType(certificate_type),
                    issuer_id,
                    independence_basis,
                    verification_method,
                    scope,
                    self._parse_evidence_lines(evidence_lines),
                    answers,
                )

        return self.jobs.submit("Record solvability certificate", epoch_id, operation)

    def record_review(
        self,
        epoch_id: str,
        snapshot_id: str,
        reviewer_id: str,
        certificate_digest: str,
        decision: str,
        finding_values: Mapping[str, bool],
        limitations: str,
        key_id: str,
        signing_key_path: str,
        public_key_path: str,
        prior_review_digest: str,
    ) -> OperatorJob:
        epoch_id = self.validate_epoch_id(epoch_id)
        findings = ReviewFindings(**dict(finding_values))

        def operation() -> Any:
            signing_path = Path(signing_key_path)
            public_path = Path(public_key_path)
            if not signing_path.is_absolute() or not public_path.is_absolute():
                raise ValueError("review key paths must be absolute")
            signing_key = signing_path.read_bytes().strip()
            public_key = public_path.read_bytes().strip()
            with local_file_lock(self.evidence.root, f"epoch-{epoch_id}"):
                controller = self._controller(epoch_id)
                snapshot = controller.snapshot_by_id(snapshot_id)
                return controller.record_human_review(
                    snapshot,
                    reviewer_id,
                    certificate_digest,
                    PromotionDecision(decision),
                    findings,
                    tuple(line.strip() for line in limitations.splitlines() if line.strip()),
                    key_id,
                    signing_key,
                    public_key,
                    prior_review_digest.strip() or None,
                )

        return self.jobs.submit("Record signed candidate decision", epoch_id, operation)

"""Restart-safe local workflow state for BBA epochs."""

from __future__ import annotations

import fcntl
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping

from bba.protocol import ExperimentManifest, digest_json


STATE_SCHEMA_VERSION = 2
PHASES = (
    "created",
    "public_running",
    "awaiting_review",
    "audit_population_frozen",
    "public_closed",
    "audited",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def local_file_lock(evidence_root: Path, lock_id: str) -> Iterator[None]:
    """Allow one local process to change one named resource."""

    if not re.fullmatch(r"[a-zA-Z0-9._-]+", lock_id):
        raise ValueError("local lock ID must be filesystem safe")
    lock_root = Path(evidence_root).resolve() / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{lock_id}.lock"
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another local process holds lock {lock_id}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LocalStateStore:
    """Store local epoch coordination data in one transactional SQLite file."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, STATE_SCHEMA_VERSION):
                raise RuntimeError(
                    f"unsupported local state schema {version}; expected {STATE_SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS epochs (
                    epoch_id TEXT PRIMARY KEY,
                    manifest_digest TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_items (
                    epoch_id TEXT NOT NULL,
                    work_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    evidence_ref TEXT,
                    evidence_digest TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (epoch_id, work_id),
                    FOREIGN KEY (epoch_id) REFERENCES epochs(epoch_id)
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    epoch_id TEXT NOT NULL,
                    work_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    PRIMARY KEY (epoch_id, work_id, attempt),
                    FOREIGN KEY (epoch_id, work_id)
                        REFERENCES work_items(epoch_id, work_id)
                );

                CREATE TABLE IF NOT EXISTS inference_reservations (
                    epoch_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    reserved_calls INTEGER NOT NULL,
                    reserved_input_tokens INTEGER NOT NULL,
                    reserved_output_tokens INTEGER NOT NULL,
                    actual_calls INTEGER,
                    actual_input_tokens INTEGER,
                    actual_output_tokens INTEGER,
                    reconciled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (epoch_id, reservation_id),
                    FOREIGN KEY (epoch_id) REFERENCES epochs(epoch_id)
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {STATE_SCHEMA_VERSION}")

    def reserve_inference(
        self,
        epoch_id: str,
        reservation_id: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        limits: Mapping[str, int],
    ) -> None:
        if min(calls, input_tokens, output_tokens) < 0:
            raise ValueError("inference reservations cannot be negative")
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM inference_reservations WHERE epoch_id = ? AND reservation_id = ?",
                (epoch_id, reservation_id),
            ).fetchone()
            if existing is not None:
                requested = (calls, input_tokens, output_tokens)
                frozen = (
                    existing["reserved_calls"],
                    existing["reserved_input_tokens"],
                    existing["reserved_output_tokens"],
                )
                if requested != frozen:
                    raise ValueError("inference reservation conflicts with frozen values")
                return
            totals = connection.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_calls ELSE reserved_calls END), 0) calls, "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_input_tokens ELSE reserved_input_tokens END), 0) input_tokens, "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_output_tokens ELSE reserved_output_tokens END), 0) output_tokens "
                "FROM inference_reservations WHERE epoch_id = ?",
                (epoch_id,),
            ).fetchone()
            projected = {
                "calls": totals["calls"] + calls,
                "input_tokens": totals["input_tokens"] + input_tokens,
                "output_tokens": totals["output_tokens"] + output_tokens,
            }
            for name, value in projected.items():
                if value > int(limits[name]):
                    raise RuntimeError(f"epoch {name.replace('_', '-')} limit would be exceeded")
            connection.execute(
                "INSERT INTO inference_reservations "
                "(epoch_id, reservation_id, reserved_calls, reserved_input_tokens, "
                "reserved_output_tokens, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (epoch_id, reservation_id, calls, input_tokens, output_tokens, now),
            )

    def reconcile_inference(
        self,
        epoch_id: str,
        reservation_id: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM inference_reservations WHERE epoch_id = ? AND reservation_id = ?",
                (epoch_id, reservation_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"inference reservation does not exist: {reservation_id}")
            actual = (calls, input_tokens, output_tokens)
            if row["reconciled"]:
                existing = (
                    row["actual_calls"], row["actual_input_tokens"], row["actual_output_tokens"]
                )
                if existing != actual:
                    raise ValueError("reconciled inference usage cannot change")
                return
            if (
                calls > row["reserved_calls"]
                or input_tokens > row["reserved_input_tokens"]
                or output_tokens > row["reserved_output_tokens"]
            ):
                raise RuntimeError("actual inference usage exceeded its reservation")
            connection.execute(
                "UPDATE inference_reservations SET actual_calls = ?, actual_input_tokens = ?, "
                "actual_output_tokens = ?, reconciled = 1, updated_at = ? "
                "WHERE epoch_id = ? AND reservation_id = ?",
                (calls, input_tokens, output_tokens, now, epoch_id, reservation_id),
            )

    def inference_usage(self, epoch_id: str) -> Dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_calls ELSE reserved_calls END), 0) calls, "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_input_tokens ELSE reserved_input_tokens END), 0) input_tokens, "
                "COALESCE(SUM(CASE WHEN reconciled = 1 THEN actual_output_tokens ELSE reserved_output_tokens END), 0) output_tokens "
                "FROM inference_reservations WHERE epoch_id = ?",
                (epoch_id,),
            ).fetchone()
            return dict(row)

    @staticmethod
    def _touch_epoch(
        connection: sqlite3.Connection,
        epoch_id: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            "UPDATE epochs SET updated_at = ? WHERE epoch_id = ?",
            (timestamp, epoch_id),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register_epoch(self, manifest: ExperimentManifest) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT manifest_digest FROM epochs WHERE epoch_id = ?",
                (manifest.epoch_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO epochs VALUES (?, ?, ?, ?, ?)",
                    (manifest.epoch_id, manifest.digest, "created", now, now),
                )
            elif row["manifest_digest"] != manifest.digest:
                raise ValueError("local epoch state has a different manifest digest")

    def set_phase(self, epoch_id: str, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown epoch phase: {phase}")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT phase FROM epochs WHERE epoch_id = ?", (epoch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"epoch state does not exist: {epoch_id}")
            if PHASES.index(phase) < PHASES.index(row["phase"]):
                return
            connection.execute(
                "UPDATE epochs SET phase = ?, updated_at = ? WHERE epoch_id = ?",
                (phase, _utc_now(), epoch_id),
            )

    def claim(
        self,
        epoch_id: str,
        work_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> bool:
        payload_digest = digest_json(payload)
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM work_items WHERE epoch_id = ? AND work_id = ?",
                (epoch_id, work_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO work_items "
                    "(epoch_id, work_id, kind, payload_digest, status, updated_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?)",
                    (epoch_id, work_id, kind, payload_digest, now),
                )
                row = connection.execute(
                    "SELECT * FROM work_items WHERE epoch_id = ? AND work_id = ?",
                    (epoch_id, work_id),
                ).fetchone()
            if row["kind"] != kind or row["payload_digest"] != payload_digest:
                raise ValueError(f"work identity conflicts with frozen payload: {work_id}")
            if row["status"] == "succeeded":
                return False
            if row["status"] == "running":
                raise RuntimeError(f"work item is already running: {work_id}")
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                "UPDATE work_items SET status = 'running', attempt_count = ?, "
                "error = NULL, updated_at = ? WHERE epoch_id = ? AND work_id = ?",
                (attempt, now, epoch_id, work_id),
            )
            connection.execute(
                "INSERT INTO attempts "
                "(epoch_id, work_id, attempt, started_at, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                (epoch_id, work_id, attempt, now),
            )
            self._touch_epoch(connection, epoch_id, now)
            return True

    def succeed(
        self,
        epoch_id: str,
        work_id: str,
        evidence_ref: str,
        evidence_digest: str,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT attempt_count, status FROM work_items "
                "WHERE epoch_id = ? AND work_id = ?",
                (epoch_id, work_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"work item does not exist: {work_id}")
            if row["status"] == "succeeded":
                return
            attempt = int(row["attempt_count"])
            connection.execute(
                "UPDATE work_items SET status = 'succeeded', evidence_ref = ?, "
                "evidence_digest = ?, error = NULL, updated_at = ? "
                "WHERE epoch_id = ? AND work_id = ?",
                (evidence_ref, evidence_digest, now, epoch_id, work_id),
            )
            connection.execute(
                "UPDATE attempts SET status = 'succeeded', finished_at = ? "
                "WHERE epoch_id = ? AND work_id = ? AND attempt = ?",
                (now, epoch_id, work_id, attempt),
            )
            self._touch_epoch(connection, epoch_id, now)

    def fail(self, epoch_id: str, work_id: str, error: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM work_items WHERE epoch_id = ? AND work_id = ?",
                (epoch_id, work_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"work item does not exist: {work_id}")
            attempt = int(row["attempt_count"])
            connection.execute(
                "UPDATE work_items SET status = 'failed', error = ?, updated_at = ? "
                "WHERE epoch_id = ? AND work_id = ?",
                (error[-4000:], now, epoch_id, work_id),
            )
            connection.execute(
                "UPDATE attempts SET status = 'failed', error = ?, finished_at = ? "
                "WHERE epoch_id = ? AND work_id = ? AND attempt = ?",
                (error[-4000:], now, epoch_id, work_id, attempt),
            )
            self._touch_epoch(connection, epoch_id, now)

    def reconcile_success(
        self,
        epoch_id: str,
        work_id: str,
        kind: str,
        payload: Mapping[str, Any],
        evidence_ref: str,
        evidence_digest: str,
    ) -> None:
        payload_digest = digest_json(payload)
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT kind, payload_digest, status, attempt_count, evidence_ref, "
                "evidence_digest FROM work_items "
                "WHERE epoch_id = ? AND work_id = ?",
                (epoch_id, work_id),
            ).fetchone()
            if row is not None and (
                row["kind"] != kind or row["payload_digest"] != payload_digest
            ):
                raise ValueError(f"work identity conflicts with immutable evidence: {work_id}")
            if row is not None and row["status"] == "succeeded" and (
                row["evidence_ref"] != evidence_ref
                or row["evidence_digest"] != evidence_digest
            ):
                raise ValueError(f"immutable evidence changed after work completion: {work_id}")
            if row is None:
                connection.execute(
                    "INSERT INTO work_items "
                    "(epoch_id, work_id, kind, payload_digest, status, evidence_ref, "
                    "evidence_digest, updated_at) VALUES (?, ?, ?, ?, 'succeeded', ?, ?, ?)",
                    (
                        epoch_id,
                        work_id,
                        kind,
                        payload_digest,
                        evidence_ref,
                        evidence_digest,
                        now,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE work_items SET status = 'succeeded', evidence_ref = ?, "
                    "evidence_digest = ?, error = NULL, updated_at = ? "
                    "WHERE epoch_id = ? AND work_id = ?",
                    (evidence_ref, evidence_digest, now, epoch_id, work_id),
                )
                if row["status"] == "running":
                    connection.execute(
                        "UPDATE attempts SET status = 'succeeded', finished_at = ? "
                        "WHERE epoch_id = ? AND work_id = ? AND attempt = ?",
                        (now, epoch_id, work_id, row["attempt_count"]),
                    )
            self._touch_epoch(connection, epoch_id, now)

    def recover_interrupted(self, epoch_id: str) -> int:
        now = _utc_now()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT work_id, attempt_count FROM work_items "
                "WHERE epoch_id = ? AND status = 'running'",
                (epoch_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE attempts SET status = 'interrupted', error = ?, finished_at = ? "
                    "WHERE epoch_id = ? AND work_id = ? AND attempt = ?",
                    (
                        "the prior local process stopped before commit",
                        now,
                        epoch_id,
                        row["work_id"],
                        row["attempt_count"],
                    ),
                )
            connection.execute(
                "UPDATE work_items SET status = 'pending', error = ?, updated_at = ? "
                "WHERE epoch_id = ? AND status = 'running'",
                ("the prior local process stopped before commit", now, epoch_id),
            )
            if rows:
                self._touch_epoch(connection, epoch_id, now)
            return len(rows)

    def status(self, epoch_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            epoch = connection.execute(
                "SELECT * FROM epochs WHERE epoch_id = ?", (epoch_id,)
            ).fetchone()
            if epoch is None:
                raise KeyError(f"epoch state does not exist: {epoch_id}")
            counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM work_items "
                    "WHERE epoch_id = ? GROUP BY status",
                    (epoch_id,),
                )
            }
            failed = [
                dict(row)
                for row in connection.execute(
                    "SELECT work_id, kind, attempt_count, error FROM work_items "
                    "WHERE epoch_id = ? AND status = 'failed' ORDER BY work_id",
                    (epoch_id,),
                )
            ]
            return {
                "epoch_id": epoch_id,
                "manifest_digest": epoch["manifest_digest"],
                "phase": epoch["phase"],
                "work_counts": counts,
                "failed_work": failed,
                "updated_at": epoch["updated_at"],
            }

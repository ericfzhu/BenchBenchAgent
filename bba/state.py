"""Public workflow-state API with conservative USD budget enforcement."""

from __future__ import annotations

from typing import Dict, Mapping

from bba._state import *  # noqa: F401,F403
from bba._state import LocalStateStore as _LocalStateStore
from bba._state import _utc_now
from bba.pricing import PriceCatalog
from bba.protocol import ExperimentManifest


STATE_SCHEMA_VERSION = 3


class LocalStateStore(_LocalStateStore):
    """State store that reserves calls, tokens, and conservative USD cost."""

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == STATE_SCHEMA_VERSION:
            return
        if version not in (0, 1, 2):
            raise RuntimeError(
                f"unsupported local state schema {version}; expected {STATE_SCHEMA_VERSION}"
            )

        # Build or migrate the version-2 tables first, then extend them without
        # discarding any local workflow or reservation records.
        super()._initialize()
        catalog = PriceCatalog()
        with self._connect() as connection:
            epoch_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(epochs)")
            }
            reservation_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(inference_reservations)"
                )
            }
            if "max_estimated_cost_usd" not in epoch_columns:
                connection.execute(
                    "ALTER TABLE epochs ADD COLUMN max_estimated_cost_usd REAL"
                )
            if "reserved_cost_usd" not in reservation_columns:
                connection.execute(
                    "ALTER TABLE inference_reservations "
                    "ADD COLUMN reserved_cost_usd REAL NOT NULL DEFAULT 0"
                )
            if "actual_cost_usd" not in reservation_columns:
                connection.execute(
                    "ALTER TABLE inference_reservations "
                    "ADD COLUMN actual_cost_usd REAL"
                )

            rows = connection.execute(
                "SELECT epoch_id, reservation_id, reserved_input_tokens, "
                "reserved_output_tokens, actual_input_tokens, actual_output_tokens, "
                "reconciled FROM inference_reservations"
            ).fetchall()
            for row in rows:
                reserved_cost = catalog.conservative_cost(
                    int(row["reserved_input_tokens"]),
                    int(row["reserved_output_tokens"]),
                )
                actual_cost = None
                if row["reconciled"]:
                    actual_cost = catalog.conservative_cost(
                        int(row["actual_input_tokens"] or 0),
                        int(row["actual_output_tokens"] or 0),
                    )
                connection.execute(
                    "UPDATE inference_reservations SET reserved_cost_usd = ?, "
                    "actual_cost_usd = ? WHERE epoch_id = ? AND reservation_id = ?",
                    (
                        reserved_cost,
                        actual_cost,
                        row["epoch_id"],
                        row["reservation_id"],
                    ),
                )
            connection.execute(f"PRAGMA user_version = {STATE_SCHEMA_VERSION}")

    def register_epoch(self, manifest: ExperimentManifest) -> None:
        now = _utc_now()
        limit = float(manifest.budget.max_estimated_cost_usd)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT manifest_digest, max_estimated_cost_usd FROM epochs "
                "WHERE epoch_id = ?",
                (manifest.epoch_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO epochs "
                    "(epoch_id, manifest_digest, phase, created_at, updated_at, "
                    "max_estimated_cost_usd) VALUES (?, ?, 'created', ?, ?, ?)",
                    (manifest.epoch_id, manifest.digest, now, now, limit),
                )
            else:
                if row["manifest_digest"] != manifest.digest:
                    raise ValueError(
                        "local epoch state has a different manifest digest"
                    )
                frozen_limit = row["max_estimated_cost_usd"]
                if frozen_limit is not None and abs(float(frozen_limit) - limit) > 1e-9:
                    raise ValueError("local epoch state has a different cost limit")
                connection.execute(
                    "UPDATE epochs SET max_estimated_cost_usd = ? WHERE epoch_id = ?",
                    (limit, manifest.epoch_id),
                )
                if self._cost_total(connection, manifest.epoch_id) > limit + 1e-9:
                    raise RuntimeError(
                        "existing conservative inference cost exceeds the epoch limit"
                    )

    @staticmethod
    def _cost_total(connection, epoch_id: str) -> float:
        row = connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN reconciled = 1 "
            "THEN actual_cost_usd ELSE reserved_cost_usd END), 0) AS cost "
            "FROM inference_reservations WHERE epoch_id = ?",
            (epoch_id,),
        ).fetchone()
        return float(row["cost"] or 0.0)

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
        reserved_cost = PriceCatalog().conservative_cost(
            input_tokens, output_tokens
        )
        with self._transaction() as connection:
            storage_id = self._reservation_storage_id(
                connection, epoch_id, reservation_id
            )
            existing = connection.execute(
                "SELECT * FROM inference_reservations "
                "WHERE epoch_id = ? AND reservation_id = ?",
                (epoch_id, storage_id),
            ).fetchone()
            if existing is not None:
                requested = (calls, input_tokens, output_tokens)
                frozen = (
                    existing["reserved_calls"],
                    existing["reserved_input_tokens"],
                    existing["reserved_output_tokens"],
                )
                if requested != frozen or abs(
                    float(existing["reserved_cost_usd"]) - reserved_cost
                ) > 1e-9:
                    raise ValueError(
                        "inference reservation conflicts with frozen values"
                    )
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
                    raise RuntimeError(
                        f"epoch {name.replace('_', '-')} limit would be exceeded"
                    )
            epoch = connection.execute(
                "SELECT max_estimated_cost_usd FROM epochs WHERE epoch_id = ?",
                (epoch_id,),
            ).fetchone()
            if epoch is None or epoch["max_estimated_cost_usd"] is None:
                raise RuntimeError("epoch cost limit is not registered")
            if (
                self._cost_total(connection, epoch_id) + reserved_cost
                > float(epoch["max_estimated_cost_usd"]) + 1e-9
            ):
                raise RuntimeError("epoch estimated-cost limit would be exceeded")

            connection.execute(
                "INSERT INTO inference_reservations "
                "(epoch_id, reservation_id, reserved_calls, reserved_input_tokens, "
                "reserved_output_tokens, reserved_cost_usd, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    epoch_id,
                    storage_id,
                    calls,
                    input_tokens,
                    output_tokens,
                    reserved_cost,
                    now,
                ),
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
        actual_cost = PriceCatalog().conservative_cost(
            input_tokens, output_tokens
        )
        with self._transaction() as connection:
            storage_id = self._reservation_storage_id(
                connection,
                epoch_id,
                reservation_id,
                legacy_fallback=True,
            )
            row = connection.execute(
                "SELECT * FROM inference_reservations "
                "WHERE epoch_id = ? AND reservation_id = ?",
                (epoch_id, storage_id),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"inference reservation does not exist: {reservation_id}"
                )
            actual = (calls, input_tokens, output_tokens)
            if row["reconciled"]:
                existing = (
                    row["actual_calls"],
                    row["actual_input_tokens"],
                    row["actual_output_tokens"],
                )
                if existing != actual or abs(
                    float(row["actual_cost_usd"] or 0.0) - actual_cost
                ) > 1e-9:
                    raise ValueError("reconciled inference usage cannot change")
                return
            if (
                calls > row["reserved_calls"]
                or input_tokens > row["reserved_input_tokens"]
                or output_tokens > row["reserved_output_tokens"]
                or actual_cost > float(row["reserved_cost_usd"]) + 1e-9
            ):
                raise RuntimeError(
                    "actual inference usage exceeded its reservation"
                )
            connection.execute(
                "UPDATE inference_reservations SET actual_calls = ?, "
                "actual_input_tokens = ?, actual_output_tokens = ?, "
                "actual_cost_usd = ?, reconciled = 1, updated_at = ? "
                "WHERE epoch_id = ? AND reservation_id = ?",
                (
                    calls,
                    input_tokens,
                    output_tokens,
                    actual_cost,
                    now,
                    epoch_id,
                    storage_id,
                ),
            )

    def inference_cost_usd(self, epoch_id: str) -> float:
        with self._connect() as connection:
            return self._cost_total(connection, epoch_id)

    def status(self, epoch_id: str) -> Dict[str, object]:
        result = super().status(epoch_id)
        with self._connect() as connection:
            epoch = connection.execute(
                "SELECT max_estimated_cost_usd FROM epochs WHERE epoch_id = ?",
                (epoch_id,),
            ).fetchone()
            result["estimated_cost_usd"] = self._cost_total(
                connection, epoch_id
            )
            result["max_estimated_cost_usd"] = (
                float(epoch["max_estimated_cost_usd"])
                if epoch and epoch["max_estimated_cost_usd"] is not None
                else None
            )
        return result

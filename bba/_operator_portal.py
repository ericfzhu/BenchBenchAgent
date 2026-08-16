"""Operator-console facade with readiness checks and local diagnostics."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from bba._operator import *  # noqa: F401,F403
from bba._operator import OperatorConsole as _OperatorConsole
from bba.catalog import CATALOG_VERSION, SERVERLESS_COHORT
from bba.dependencies import LocalWheelCatalog
from bba.gcp import discover_gcp_project
from bba.pricing import PriceCatalog
from bba.runtime import SecureSandbox


class OperatorConsole(_OperatorConsole):
    """Local development portal API layered over the production controller."""

    DIAGNOSTIC_ACTIONS = {
        "sandbox": "Check local sandbox",
        "catalog": "Inspect model catalog",
        "tests": "Run local test suite",
    }

    def readiness(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        try:
            sandbox = SecureSandbox()
            checks.append({
                "id": "sandbox",
                "label": "Generated-code sandbox",
                "status": "passed" if sandbox.available else "failed",
                "detail": (
                    sandbox.backend
                    if sandbox.available
                    else sandbox.unavailable_reason
                    or "The required OS sandbox is unavailable."
                ),
                "required": True,
            })
        except Exception as exc:
            checks.append({
                "id": "sandbox",
                "label": "Generated-code sandbox",
                "status": "failed",
                "detail": str(exc),
                "required": True,
            })

        try:
            project = discover_gcp_project()
            checks.append({
                "id": "adc",
                "label": "Application Default Credentials",
                "status": "passed",
                "detail": f"Project {project}",
                "required": True,
            })
        except Exception as exc:
            checks.append({
                "id": "adc",
                "label": "Application Default Credentials",
                "status": "failed",
                "detail": str(exc),
                "required": True,
            })

        prices = PriceCatalog()
        missing_prices = sorted(
            identity.model
            for identity in SERVERLESS_COHORT
            if identity.model not in prices.models
        )
        checks.append({
            "id": "pricing",
            "label": "Frozen price catalog",
            "status": "passed" if not missing_prices else "failed",
            "detail": (
                f"{len(prices.models)} routes · {prices.safety_multiplier:g}× safety factor"
                if not missing_prices
                else "Missing: " + ", ".join(missing_prices)
            ),
            "required": True,
        })

        wheels = LocalWheelCatalog(
            Path(__file__).resolve().parent / "data" / "dependency-wheels"
        )
        checks.append({
            "id": "wheels",
            "label": "Candidate dependencies",
            "status": "warning" if not wheels.entries else "passed",
            "detail": (
                "Standard-library-only candidate packages"
                if not wheels.entries
                else f"{len(wheels.entries)} approved wheel versions"
            ),
            "required": False,
        })

        try:
            adk_version = importlib.metadata.version("google-adk")
        except importlib.metadata.PackageNotFoundError:
            adk_version = "not installed"
        return {
            "ready": all(
                item["status"] == "passed"
                for item in checks
                if item["required"]
            ),
            "checks": checks,
            "catalog_version": CATALOG_VERSION,
            "model_count": len(SERVERLESS_COHORT),
            "python": platform.python_version(),
            "google_adk": adk_version,
            "evidence_root": str(self.evidence.root),
        }

    def _run_command(self, command: Sequence[str]) -> str:
        with tempfile.TemporaryFile() as output:
            with self._process_lock:
                if self._closed:
                    raise RuntimeError(
                        "the console stopped before the operation started"
                    )
                process = subprocess.Popen(
                    list(command),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    cwd=str(Path.cwd()),
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
            output.seek(max(0, length - 64000))
            text = output.read().decode(
                "utf-8", errors="replace"
            ).strip()
        if return_code:
            raise RuntimeError(
                text or f"diagnostic stopped with status {return_code}"
            )
        return text

    def run_diagnostic(self, action: str):
        if action not in self.DIAGNOSTIC_ACTIONS:
            raise ValueError("unknown diagnostic action")
        if action == "sandbox":
            command = [sys.executable, "-m", "bba.cli", "sandbox-status"]
        elif action == "catalog":
            command = [sys.executable, "-m", "bba.cli", "catalog"]
        else:
            command = [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ]
        return self.jobs.submit(
            self.DIAGNOSTIC_ACTIONS[action],
            None,
            lambda: self._run_command(command),
        )

    @staticmethod
    def _workflow(phase: str, *, preflight: bool, audit_frozen: bool,
                  public_closed: bool, audited: bool) -> list[dict[str, Any]]:
        public_complete = phase in {
            "awaiting_review",
            "audit_population_frozen",
            "public_closed",
            "audited",
        }
        definitions = [
            ("setup", "Setup", True),
            ("preflight", "Paid preflight", preflight),
            ("run", "Public tournament", public_complete),
            ("review", "Human review", audit_frozen),
            ("freeze-audit", "Freeze audit inputs", audit_frozen),
            ("close", "Publish results", public_closed),
            ("audit", "Sealed audit", audited),
        ]
        first_incomplete = next(
            (key for key, _label, complete in definitions if not complete),
            None,
        )
        return [
            {
                "key": key,
                "label": label,
                "complete": complete,
                "current": key == first_incomplete,
            }
            for key, label, complete in definitions
        ]

    def epoch(self, epoch_id: str) -> dict[str, Any]:
        value = super().epoch(epoch_id)
        root = self.evidence.epoch_root(epoch_id)
        preflight_path = root / "preflight" / "vertex.json"
        preflight = False
        if preflight_path.is_file():
            try:
                preflight = bool(json.loads(
                    preflight_path.read_text(encoding="utf-8")
                ).get("passed"))
            except (OSError, json.JSONDecodeError):
                preflight = False
        audit_frozen = (root / "audit" / "public-population.json").is_file()
        public_closed = (root / "evaluation" / "public.json").is_file()
        audited = (root / "audit" / "holdout.json").is_file()
        phase = value["phase"]
        value["workflow"] = self._workflow(
            phase,
            preflight=preflight,
            audit_frozen=audit_frozen,
            public_closed=public_closed,
            audited=audited,
        )
        value["review_open"] = not audit_frozen and not public_closed
        value["action_states"] = {
            "preflight": {
                "enabled": not audit_frozen and not public_closed,
                "complete": preflight,
                "hint": "Verify every frozen Vertex route and tool contract.",
            },
            "run": {
                "enabled": preflight and phase in {"created", "public_running"},
                "complete": phase not in {"created", "public_running"},
                "hint": "Run or resume creator, validation, and solver work.",
            },
            "freeze-audit": {
                "enabled": phase in {"awaiting_review", "audit_population_frozen"},
                "complete": audit_frozen,
                "hint": "Commit public evaluator inputs and close review.",
            },
            "close": {
                "enabled": audit_frozen and not audited,
                "complete": public_closed,
                "hint": "Publish the immutable public matrix and rankings.",
            },
            "audit": {
                "enabled": public_closed,
                "complete": audited,
                "hint": "Open committed holdouts and validate the evaluator.",
            },
        }
        value["usage"] = self.state.inference_usage(epoch_id)
        value["usage"]["estimated_cost_usd"] = self.state.inference_cost_usd(
            epoch_id
        )
        value["usage"]["max_estimated_cost_usd"] = value.get(
            "max_estimated_cost_usd"
        )
        value["preflight_passed"] = preflight
        return value

    def candidate(self, epoch_id: str, snapshot_id: str) -> dict[str, Any]:
        value = super().candidate(epoch_id, snapshot_id)
        root = self.evidence.epoch_root(epoch_id)
        value["review_open"] = not (
            (root / "audit" / "public-population.json").is_file()
            or (root / "evaluation" / "public.json").is_file()
        )
        return value

"""Local development portal and spatial command deck tests."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from bba.cli import build_parser
from bba.operator import OperatorConsole, OperatorJobQueue
from bba.web import create_app, run_console


class FakeEvidence:
    root = Path("/tmp/bba-console-test")

    def epoch_root(self, epoch_id: str) -> Path:
        return self.root / "epochs" / epoch_id


class FakeJobs:
    def __init__(self):
        self.items = {
            "job-1": {
                "job_id": "job-1",
                "label": "Preflight",
                "status": "succeeded",
                "epoch_id": "epoch-one",
                "created_at": "2026-08-12T01:00:00+00:00",
                "started_at": "2026-08-12T01:00:01+00:00",
                "finished_at": "2026-08-12T01:00:05+00:00",
                "output": "All checks passed.",
                "error": None,
            }
        }

    def recent(self, limit=10):
        return list(self.items.values())[:limit]

    def get(self, job_id):
        return self.items.get(job_id)


class FakeConsole:
    evidence = FakeEvidence()
    jobs = FakeJobs()
    EPOCH_ACTIONS = OperatorConsole.EPOCH_ACTIONS
    DIAGNOSTIC_ACTIONS = OperatorConsole.DIAGNOSTIC_ACTIONS

    def readiness(self):
        return {
            "ready": True,
            "checks": [
                {
                    "id": "sandbox",
                    "label": "Generated-code sandbox",
                    "status": "passed",
                    "detail": "linux-bubblewrap",
                    "required": True,
                },
                {
                    "id": "adc",
                    "label": "Application Default Credentials",
                    "status": "passed",
                    "detail": "Project bba-test-project",
                    "required": True,
                },
            ],
            "catalog_version": "catalog-v1",
            "model_count": 2,
            "python": "3.12.0",
            "google_adk": "2.6.3",
            "evidence_root": str(self.evidence.root),
        }

    def list_epochs(self):
        return [{
            "epoch_id": "epoch-one",
            "phase": "awaiting_review",
            "catalog_version": "catalog-v1",
            "snapshots": 3,
            "solver_cells": 108,
            "updated_at": "2026-08-12T01:02:03+00:00",
        }]

    def create_epoch(self, epoch_id):
        return SimpleNamespace(job_id=f"job-create-{epoch_id}", label=f"Create epoch {epoch_id}", status="queued")

    def run_epoch_action(self, epoch_id, action):
        if action not in self.EPOCH_ACTIONS and action not in {"run", "close"}:
            raise ValueError("unknown action")
        return SimpleNamespace(job_id=f"job-{action}", label=self.EPOCH_ACTIONS.get(action, action), status="queued")

    def run_diagnostic(self, action):
        return SimpleNamespace(job_id="diagnostic-one", label="Run diagnostic", status="queued")

    def record_certificate(self, epoch_id, snapshot_id, *args, **kwargs):
        return SimpleNamespace(job_id="job-cert-1", label="Record Certificate", status="queued")

    def record_review(self, epoch_id, snapshot_id, *args, **kwargs):
        return SimpleNamespace(job_id="job-review-1", label="Record Review", status="queued")

    def epoch(self, epoch_id):
        return {
            "epoch_id": epoch_id,
            "phase": "awaiting_review",
            "snapshots": 3,
            "solver_cells": 108,
            "promotions": 1,
            "approved": 1,
            "review_open": True,
            "failed_work": [],
            "max_estimated_cost_usd": 500.0,
            "usage": {
                "calls": 42,
                "input_tokens": 1000,
                "output_tokens": 500,
                "estimated_cost_usd": 12.5,
                "max_estimated_cost_usd": 500.0,
            },
            "workflow": [
                {"key": "setup", "label": "Setup", "complete": True, "current": False},
                {"key": "preflight", "label": "Paid preflight", "complete": True, "current": False},
                {"key": "run", "label": "Public tournament", "complete": True, "current": False},
                {"key": "review", "label": "Human review", "complete": False, "current": True},
                {"key": "freeze-audit", "label": "Freeze audit inputs", "complete": False, "current": False},
                {"key": "close", "label": "Publish results", "complete": False, "current": False},
                {"key": "audit", "label": "Sealed audit", "complete": False, "current": False},
            ],
            "action_states": {
                "preflight": {"enabled": True, "complete": True, "hint": "Check routes."},
                "run": {"enabled": False, "complete": True, "hint": "Run work."},
                "freeze-audit": {"enabled": True, "complete": False, "hint": "Freeze inputs."},
                "close": {"enabled": False, "complete": False, "hint": "Publish."},
                "audit": {"enabled": False, "complete": False, "hint": "Audit."},
            },
            "manifest": {
                "catalog_version": "catalog-v1",
                "created_at": "2026-08-12T01:00:00+00:00",
                "gcp_project": "project",
                "gcp_location": "global",
                "models": 2,
                "rounds": 3,
                "solver_repetitions": 3,
            },
            "candidates": [{
                "snapshot_id": "candidate-one",
                "model": "gemini-test",
                "round": 2,
                "status": "active",
                "solver_cells": 6,
                "best_solver_median": 0.2,
                "certificate_count": 1,
            }],
        }

    def candidate(self, epoch_id, snapshot_id):
        return {
            "snapshot_id": snapshot_id,
            "model": "gemini-test",
            "round": 2,
            "status": "awaiting_review",
            "design_digest": "d" * 64,
            "best_solver_median": 0.2,
            "panel_median": 0.1,
            "solver_cells": 6,
            "reviewed": False,
            "review_open": True,
            "certificates": [{
                "digest": "c" * 64,
                "certificate_type": "independent_reference",
                "issuer_id": "issuer",
            }],
            "promotions": [],
            "certificate_item_ids": [f"item-{index}" for index in range(6)],
            "final_round": True,
        }

    def results(self, epoch_id):
        return {
            "public": {
                "creator_rankings": {"final_round": [{
                    "rank": 1,
                    "creator": "creator-one",
                    "status": "active",
                    "best_solver_median": 0.2,
                    "panel_median": 0.1,
                }], "blind_round": [{
                    "rank": 1,
                    "creator": "creator-one",
                    "status": "awaiting_review",
                    "best_solver_median": 0.3,
                    "panel_median": 0.2,
                }]},
                "solver_ranking": [{
                    "rank": 1,
                    "solver": "solver-one",
                    "macro_accuracy": 0.8,
                    "canonical_benchmarks": 1,
                    "ci95": [0.7, 0.9],
                }],
                "matrix": {"candidate-one": {"solver-one": {
                    "complete": True,
                    "median_accuracy": 0.8,
                    "states": ["success"],
                }}},
            },
            "audit": {"status": "validated", "targets": {"hidden_only": {
                "spearman": 0.9,
                "pairwise": {"accuracy": 0.8},
                "selection_at_quartile": {"utility_recovery": 0.95},
                "defect_sensitivity": {"accuracy": 1.0},
            }}},
        }

    def observability(self, epoch_id):
        return {
            "epoch_id": epoch_id,
            "active": 0,
            "failures": 0,
            "totals": {
                "invocations": 2,
                "model_calls": 5,
                "tool_calls": 4,
                "total_tokens": 123,
                "prompt_tokens": 80,
                "output_tokens": 43,
                "duration_ms": 2500.0,
            },
            "models": [{
                "identity": "creator-one",
                "invocations": 2,
                "failures": 0,
                "model_calls": 5,
                "tool_calls": 4,
                "total_tokens": 123,
                "duration_ms": 2500.0,
            }],
            "recent": [{
                "status": "success",
                "role": "creator",
                "identity": "creator-one",
                "model_calls": 3,
                "tool_calls": 2,
                "total_tokens": 70,
                "duration_ms": 1500.0,
                "error_type": None,
            }],
            "tracing": {
                "enabled": True,
                "endpoint": "http://127.0.0.1:4318/v1/traces",
                "content_captured": False,
            },
        }


class TestWebConsole(unittest.TestCase):
    def setUp(self):
        self.app = create_app(FakeConsole())
        self.client = TestClient(self.app)

    def test_spatial_command_deck_and_spa_routes(self):
        # 1. Spatial command deck at root /
        deck = self.client.get("/")
        self.assertEqual(deck.status_code, 200)
        self.assertIn("Command Deck", deck.text)
        self.assertIn('id="root"', deck.text)
        self.assertEqual(deck.headers["x-frame-options"], "DENY")
        self.assertIn(
            "frame-ancestors 'none'",
            deck.headers["content-security-policy"],
        )
        self.assertIn(
            "connect-src 'self'",
            deck.headers["content-security-policy"],
        )

        # Check static dist bundle
        bundle = self.client.get("/static/dist/app.bundle.js")
        self.assertEqual(bundle.status_code, 200)

        # 2. SPA routes serve the HTML shell
        epochs = self.client.get("/epochs")
        self.assertEqual(epochs.status_code, 200)
        self.assertIn('id="root"', epochs.text)

        epoch_page = self.client.get("/epochs/epoch-one")
        self.assertEqual(epoch_page.status_code, 200)
        self.assertIn('id="root"', epoch_page.text)

        jobs_page = self.client.get("/jobs")
        self.assertEqual(jobs_page.status_code, 200)
        self.assertIn('id="root"', jobs_page.text)

    def test_reactive_json_api_endpoints(self):
        # 1. System state API
        sys_res = self.client.get("/api/system/state")
        self.assertEqual(sys_res.status_code, 200)
        sys_data = sys_res.json()
        self.assertIn("system", sys_data)
        self.assertTrue(sys_data["system"]["ready"])
        self.assertEqual(sys_data["csrf_token"], self.app.state.csrf_token)
        self.assertIn("graph", sys_data)

        # 2. Epoch state API
        ep_res = self.client.get("/api/epoch/epoch-one/state")
        self.assertEqual(ep_res.status_code, 200)
        ep_data = ep_res.json()
        self.assertEqual(ep_data["epoch_id"], "epoch-one")
        self.assertEqual(ep_data["phase"], "awaiting_review")
        self.assertIn("graph", ep_data)
        self.assertIn("candidates", ep_data)
        self.assertIn("results", ep_data)
        self.assertIn("observability", ep_data)

        # 3. Candidate details API
        cand_res = self.client.get("/api/epoch/epoch-one/candidates/candidate-one")
        self.assertEqual(cand_res.status_code, 200)
        cand_data = cand_res.json()
        self.assertIn("candidate", cand_data)
        self.assertEqual(cand_data["candidate"]["snapshot_id"], "candidate-one")

        # 4. Jobs list and job details API
        jobs_res = self.client.get("/api/jobs")
        self.assertEqual(jobs_res.status_code, 200)
        jobs_data = jobs_res.json()
        self.assertIn("jobs", jobs_data)
        self.assertEqual(len(jobs_data["jobs"]), 1)

        job_detail = self.client.get("/api/jobs/job-1")
        self.assertEqual(job_detail.status_code, 200)
        self.assertEqual(job_detail.json()["job_id"], "job-1")

        bad_job = self.client.get("/api/jobs/nonexistent-job")
        self.assertEqual(bad_job.status_code, 404)

        # 5. JSON Action execution API with CSRF
        action_res = self.client.post(
            "/api/epoch/epoch-one/action",
            json={
                "action": "preflight",
                "csrf_token": self.app.state.csrf_token,
                "confirmed": "yes",
            },
        )
        self.assertEqual(action_res.status_code, 200)
        action_data = action_res.json()
        self.assertEqual(action_data["job_id"], "job-preflight")

        # 6. Action parameter route
        action_param_res = self.client.post(
            "/api/epoch/epoch-one/action/preflight",
            json={
                "csrf_token": self.app.state.csrf_token,
                "confirmed": "yes",
            },
        )
        self.assertEqual(action_param_res.status_code, 200)

        # 7. Certificate recording API
        cert_res = self.client.post(
            "/api/epoch/epoch-one/candidates/candidate-one/certificate",
            json={
                "csrf_token": self.app.state.csrf_token,
                "confirmed": "yes",
                "certificate_type": "independent_reference",
                "issuer_id": "issuer-1",
                "independence_basis": "independent team",
                "verification_method": "re-execution",
                "scope": "all",
                "evidence_lines": "test=test.txt",
            },
        )
        self.assertEqual(cert_res.status_code, 200)
        self.assertEqual(cert_res.json()["status"], "ok")

        # 8. Review recording API
        review_res = self.client.post(
            "/api/epoch/epoch-one/candidates/candidate-one/review",
            json={
                "csrf_token": self.app.state.csrf_token,
                "confirmed": "yes",
                "reviewer_id": "reviewer-1",
                "certificate_digest": "cert-1",
                "decision": "promote_v1_canonical",
                "findings": {"named_capability_valid": True},
                "limitations": "none",
                "key_id": "key-1",
                "signing_key_path": "/tmp/key",
                "public_key_path": "/tmp/pub",
            },
        )
        self.assertEqual(review_res.status_code, 200)
        self.assertEqual(review_res.json()["status"], "ok")

        # 9. Diagnostics API
        diag_res = self.client.post(
            "/api/diagnostics/tests",
            json={
                "csrf_token": self.app.state.csrf_token,
            },
        )
        self.assertEqual(diag_res.status_code, 200)
        self.assertEqual(diag_res.json()["job_id"], "diagnostic-one")

        # 10. Create epoch API
        create_res = self.client.post(
            "/api/epochs",
            json={
                "epoch_id": "new-epoch",
                "csrf_token": self.app.state.csrf_token,
            },
        )
        self.assertEqual(create_res.status_code, 200)
        self.assertEqual(create_res.json()["job_id"], "job-create-new-epoch")

        # 11. Missing CSRF / confirmation fails
        bad_action = self.client.post(
            "/api/epoch/epoch-one/action",
            json={
                "action": "preflight",
                "csrf_token": "wrong-token",
                "confirmed": "yes",
            },
        )
        self.assertEqual(bad_action.status_code, 400)

    def test_post_requires_csrf_confirmation_and_same_origin(self):
        missing = self.client.post(
            "/api/epoch/epoch-one/action",
            json={"action": "run", "csrf_token": self.app.state.csrf_token},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("confirm this operation", missing.json()["message"])

        bad_origin = self.client.get(
            "/", headers={"origin": "https://evil.example"}
        )
        self.assertEqual(bad_origin.status_code, 403)

        bad_host = self.client.get("http://evil.example/")
        self.assertEqual(bad_host.status_code, 400)

    def test_console_binds_only_to_ipv4_loopback(self):
        with patch("bba.web.uvicorn.run") as run:
            run_console(Path("/tmp/bba-console-bind-test"), port=9876)
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 9876)

    def test_cli_exposes_web_and_operator_commands(self):
        args_web = build_parser().parse_args(["web", "--port", "9999"])
        self.assertEqual(args_web.port, 9999)
        self.assertEqual(args_web.evidence_root, ".bba")

        args_op = build_parser().parse_args(["operator", "--port", "7777"])
        self.assertEqual(args_op.port, 7777)
        self.assertEqual(args_op.evidence_root, ".bba")

    def test_job_queue_serializes_mutations(self):
        queue = OperatorJobQueue()
        release = threading.Event()
        try:
            first = queue.submit(
                "First", "epoch-one", lambda: (release.wait(1), "done")[1]
            )
            deadline = time.time() + 2
            while queue.get(first.job_id)["status"] != "running":
                self.assertLess(time.time(), deadline)
                time.sleep(0.01)
            with self.assertRaises(RuntimeError):
                queue.submit("Second", "epoch-one", lambda: "not run")
            release.set()
            while queue.get(first.job_id)["status"] not in {
                "succeeded",
                "failed",
            }:
                self.assertLess(time.time(), deadline)
                time.sleep(0.01)
            self.assertEqual(queue.get(first.job_id)["output"], "done")
        finally:
            queue.close()

    def test_console_shutdown_terminates_an_active_cli_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            console = OperatorConsole(Path(temporary))
            process = Mock()
            process.poll.return_value = None
            console._active_process = process
            console.close()
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5)

    def test_epoch_id_validation_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            console = OperatorConsole(Path(temporary))
            try:
                with self.assertRaises(ValueError):
                    console.create_epoch("../outside")
            finally:
                console.jobs.close()


if __name__ == "__main__":
    unittest.main()

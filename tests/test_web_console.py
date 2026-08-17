"""Local development portal tests."""

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


class FakeJobs:
    def __init__(self):
        self.items = {}

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
        raise AssertionError("not used")

    def run_epoch_action(self, epoch_id, action):
        raise AssertionError("missing confirmation should stop this call")

    def run_diagnostic(self, action):
        return SimpleNamespace(job_id="diagnostic-one")

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

    def test_workspace_epoch_candidate_and_rankings_render(self):
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Build, test, and run epochs", dashboard.text)
        self.assertIn("Diagnostics", dashboard.text)
        self.assertIn("Generated-code sandbox", dashboard.text)
        self.assertIn("epoch-one", dashboard.text)
        self.assertEqual(dashboard.headers["x-frame-options"], "DENY")
        self.assertIn(
            "frame-ancestors 'none'",
            dashboard.headers["content-security-policy"],
        )

        epoch = self.client.get("/epochs/epoch-one")
        self.assertEqual(epoch.status_code, 200)
        self.assertIn("Workflow", epoch.text)
        self.assertIn("Human review", epoch.text)
        self.assertIn("Conservative cost", epoch.text)
        self.assertIn("Candidate benchmarks", epoch.text)
        self.assertIn("Run or resume public epoch", epoch.text)

        candidate = self.client.get(
            "/epochs/epoch-one/candidates/candidate-one"
        )
        self.assertEqual(candidate.status_code, 200)
        self.assertIn("Record solvability evidence", candidate.text)
        self.assertIn("Record a signed candidate decision", candidate.text)
        self.assertIn("item-5", candidate.text)

        rankings = self.client.get("/epochs/epoch-one/results")
        self.assertEqual(rankings.status_code, 200)
        self.assertIn("Final creator ranking", rankings.text)
        self.assertIn("Solver ranking", rankings.text)
        self.assertIn("Spearman agreement", rankings.text)
        self.assertIn("Creator-by-solver matrix", rankings.text)

        activity = self.client.get("/epochs/epoch-one/observability")
        self.assertEqual(activity.status_code, 200)
        self.assertIn("Google ADK observability", activity.text)
        self.assertIn("Recent ADK invocations", activity.text)
        self.assertIn("Local OTLP export is on", activity.text)

    def test_diagnostic_route_uses_the_serialized_job_queue(self):
        response = self.client.post(
            "/diagnostics/tests",
            data={"csrf_token": self.app.state.csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/jobs/diagnostic-one")

    def test_post_requires_csrf_confirmation_and_same_origin(self):
        missing = self.client.post(
            "/epochs/epoch-one/actions/run",
            data={"csrf_token": self.app.state.csrf_token},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("confirm this operation", missing.text)

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

    def test_cli_exposes_web_command(self):
        args = build_parser().parse_args(["web", "--port", "9999"])
        self.assertEqual(args.port, 9999)
        self.assertEqual(args.evidence_root, ".bba")

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

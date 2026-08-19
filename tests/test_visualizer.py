"""Unit tests for the spatial visualizer state serializer and graph model."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bba.visualizer import (
    DEFAULT_CATEGORIES,
    DEFAULT_CIRCUITS,
    DEFAULT_NODES,
    VisualizerStateSerializer,
)


class FakeConsole:
    class Evidence:
        root = Path("/tmp/bba-vis-test")

        def epoch_root(self, epoch_id: str) -> Path:
            return self.root / "epochs" / epoch_id

    class Jobs:
        def recent(self, limit=10):
            return [{"job_id": "job-1", "label": "Preflight", "status": "succeeded", "epoch_id": "epoch-1"}]

    evidence = Evidence()
    jobs = Jobs()
    DIAGNOSTIC_ACTIONS = {"tests": "Run tests", "sandbox": "Check sandbox"}
    EPOCH_ACTIONS = {"preflight": "Preflight", "run": "Run"}

    def readiness(self):
        return {
            "ready": True,
            "checks": [{"id": "sandbox", "label": "Sandbox", "status": "passed", "required": True}],
            "catalog_version": "catalog-v1",
            "model_count": 3,
            "python": "3.12.0",
            "google_adk": "2.6.3",
            "evidence_root": str(self.evidence.root),
            "quota": {"utilization": 0.5},
        }

    def list_epochs(self):
        return [{"epoch_id": "epoch-1", "phase": "awaiting_review", "snapshots": 2}]

    def epoch(self, epoch_id: str):
        return {
            "epoch_id": epoch_id,
            "phase": "awaiting_review",
            "snapshots": 2,
            "solver_cells": 12,
            "approved": 1,
            "review_open": True,
            "manifest": {"catalog_version": "catalog-v1", "models": 3, "rounds": 2, "solver_repetitions": 3},
            "workflow": [{"key": "setup", "label": "Setup", "complete": True, "current": False}],
            "action_states": {"preflight": {"enabled": True, "complete": True, "hint": "Preflight"}},
            "usage": {"calls": 10, "input_tokens": 1000, "output_tokens": 500, "estimated_cost_usd": 4.5, "max_estimated_cost_usd": 500.0},
            "candidates": [
                {
                    "snapshot_id": "cand-1",
                    "model": "model-alpha",
                    "round": 1,
                    "status": "active",
                    "validation_passed": True,
                    "solver_cells": 6,
                    "best_solver_median": 0.3,
                    "reviewed": True,
                }
            ],
        }

    def candidate(self, epoch_id: str, snapshot_id: str):
        return {
            "snapshot_id": snapshot_id,
            "model": "model-alpha",
            "round": 1,
            "status": "active",
            "design_digest": "a" * 64,
            "best_solver_median": 0.3,
            "panel_median": 0.2,
            "solver_cells": 6,
            "reviewed": True,
            "review_open": True,
            "certificates": [],
            "promotions": [],
            "certificate_item_ids": ["item-1"],
            "final_round": True,
        }

    def results(self, epoch_id: str):
        return {
            "public": {
                "creator_rankings": {"final_round": [{"rank": 1, "creator": "model-alpha", "status": "active", "best_solver_median": 0.3, "panel_median": 0.2}]},
                "solver_ranking": [{"rank": 1, "solver": "solver-alpha", "macro_accuracy": 0.7, "ci95": [0.6, 0.8]}],
                "matrix": {},
            },
            "audit": None,
        }

    def observability(self, epoch_id: str):
        return {
            "totals": {"invocations": 5, "model_calls": 10, "tool_calls": 8, "total_tokens": 1500, "duration_ms": 3200},
            "models": [],
            "recent": [],
            "tracing": {"enabled": True, "endpoint": "http://127.0.0.1:4318"},
        }


class TestVisualizer(unittest.TestCase):
    def setUp(self):
        self.console = FakeConsole()

    def test_default_graph_layout_structure(self):
        layout = VisualizerStateSerializer.get_default_graph_layout()
        self.assertIn("categories", layout)
        self.assertIn("nodes", layout)
        self.assertIn("circuits", layout)

        category_ids = {c["id"] for c in layout["categories"]}
        self.assertEqual(category_ids, {"tournament_loop", "supporting_infrastructure", "models_and_engines"})

        node_ids = {n["id"] for n in layout["nodes"]}
        expected_nodes = {
            "archive_vault", "seed_foundry", "creator_cohort", "sandbox_chamber",
            "freeze_seal", "package_validator", "solver_panel", "matrix_scorer",
            "promotion_registry", "quota_governor", "session_budget", "sqlite_wal",
            "evidence_store", "adk_runtime", "bubblewrap_sandbox", "multiprovider_fleet"
        }
        self.assertEqual(node_ids, expected_nodes)

        for node in layout["nodes"]:
            self.assertIn("code", node)
            self.assertIn("label", node)
            self.assertIn("category", node)
            self.assertIn("x", node)
            self.assertIn("y", node)
            self.assertIn("width", node)
            self.assertIn("depth", node)
            self.assertIn("height", node)
            self.assertIn("summary", node)
            self.assertIn("invariants", node)

        self.assertGreater(len(layout["circuits"]), 5)
        for circuit in layout["circuits"]:
            self.assertIn("id", circuit)
            self.assertIn("points", circuit)
            self.assertGreaterEqual(len(circuit["points"]), 2)

    def test_serialize_system_state(self):
        state = VisualizerStateSerializer.serialize_system_state(self.console)
        self.assertIn("system", state)
        self.assertTrue(state["system"]["ready"])
        self.assertEqual(state["system"]["catalog_version"], "catalog-v1")
        self.assertEqual(len(state["epochs"]), 1)
        self.assertEqual(state["epochs"][0]["epoch_id"], "epoch-1")
        self.assertIn("graph", state)

    def test_serialize_epoch_state(self):
        state = VisualizerStateSerializer.serialize_epoch_state(self.console, "epoch-1")
        self.assertEqual(state["epoch_id"], "epoch-1")
        self.assertEqual(state["phase"], "awaiting_review")
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(state["candidates"][0]["model"], "model-alpha")
        self.assertIn("graph", state)
        self.assertIn("results", state)
        self.assertIn("observability", state)
        self.assertIn("action_states", state)

    def test_serialize_candidate_details_with_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.console.evidence.root = temp_path
            design_dir = temp_path / "epochs" / "epoch-1" / "candidates" / "cand-1" / "design"
            design_dir.mkdir(parents=True)
            (design_dir / "benchmark.py").write_text("print('hello')", encoding="utf-8")
            (design_dir / "README.md").write_text("# Test Benchmark", encoding="utf-8")

            details = VisualizerStateSerializer.serialize_candidate_details(
                self.console, "epoch-1", "cand-1"
            )
            self.assertIn("candidate", details)
            self.assertEqual(len(details["files"]), 2)

    def test_to_excalidraw_scene(self):
        layout = VisualizerStateSerializer.get_default_graph_layout()
        scene = VisualizerStateSerializer.to_excalidraw_scene(
            layout["nodes"], layout["circuits"], layout["categories"]
        )
        self.assertEqual(scene["type"], "excalidraw")
        self.assertEqual(scene["version"], 2)
        self.assertIn("elements", scene)
        self.assertIn("appState", scene)
        self.assertTrue(len(scene["elements"]) > 20)

        # Verify all text elements have monospace font family (fontFamily == 3)
        text_elements = [e for e in scene["elements"] if e.get("type") == "text"]
        self.assertTrue(len(text_elements) > 10)
        for t in text_elements:
            self.assertEqual(t.get("fontFamily"), 3, f"Element {t.get('id')} must use monospace font family 3")
            self.assertIn("text", t)
            self.assertIn("originalText", t)
            self.assertEqual(t["text"], t["originalText"])

        # Verify node elements have customData.nodeId
        node_elements = [e for e in scene["elements"] if e.get("customData", {}).get("type") == "node"]
        self.assertTrue(len(node_elements) > 0)
        node_ids = {e["customData"]["nodeId"] for e in node_elements}
        self.assertIn("archive_vault", node_ids)
        self.assertIn("creator_cohort", node_ids)

        # Verify circuit arrow elements and orthogonal points
        arrow_elements = [e for e in scene["elements"] if e.get("type") == "arrow"]
        self.assertTrue(len(arrow_elements) > 0)
        for arrow in arrow_elements:
            points = arrow.get("points", [])
            self.assertGreaterEqual(len(points), 2)
            # Check orthogonality: each segment must change only X or only Y
            for i in range(len(points) - 1):
                p1 = points[i]
                p2 = points[i + 1]
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                self.assertTrue(dx == 0 or dy == 0, f"Arrow {arrow.get('id')} segment {i} not orthogonal: {p1} -> {p2}")


if __name__ == "__main__":
    unittest.main()

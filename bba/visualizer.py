"""Spatial visualizer state serializer and architectural graph model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from bba.operator import OperatorConsole
from bba.protocol import SolvabilityCertificateType, PromotionDecision


DEFAULT_CATEGORIES = [
    {
        "id": "tournament_loop",
        "label": "THE TOURNAMENT LOOP",
        "description": "Core iterative creation, validation, and evaluation pipeline",
    },
    {
        "id": "supporting_infrastructure",
        "label": "SUPPORTING INFRASTRUCTURE",
        "description": "Local stores, rate governors, and budget enforcement",
    },
    {
        "id": "models_and_engines",
        "label": "MODELS & ENGINES",
        "description": "Underlying execution engines, sandbox isolation, and model fleet",
    },
]

DEFAULT_NODES = [
    # THE TOURNAMENT LOOP
    {
        "id": "archive_vault",
        "code": "P",
        "label": "Archive Vault",
        "subtitle": "Historical Benchmark Pool",
        "category": "tournament_loop",
        "badge": 2,
        "x": 3,
        "y": 4,
        "z": 0,
        "width": 3.8,
        "depth": 3.8,
        "height": 1.8,
        "style": "layered",
        "summary": "Central repository of frozen candidate benchmarks and baseline seed tasks across all completed rounds.",
        "description": "The Archive Vault holds content-addressed candidate benchmarks, design snapshots, and historical execution results. Every saved candidate is immutable and content-addressed with SHA-256 digests.",
        "invariants": [
            "Snapshots are strictly append-only and cannot be mutated or overwritten in place.",
            "All artifacts are verifiable by matching their SHA-256 tree digest against recorded manifest records.",
            "Historical candidates remain accessible for longitudinal regression and stability testing.",
        ],
    },
    {
        "id": "seed_foundry",
        "code": "I",
        "label": "Seed Foundry",
        "subtitle": "Task Synthesis & Instances",
        "category": "tournament_loop",
        "badge": 1,
        "x": 3,
        "y": 10,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.6,
        "style": "crosshatch",
        "summary": "Generates deterministic problem instances and random environmental parameters for creator evaluation.",
        "description": "The Seed Foundry manages randomized test generation, ensuring deterministic reproducibility while preventing data leakage across tournament iterations.",
        "invariants": [
            "RNG seeds are recorded alongside evaluation instance manifests.",
            "Test data instances are generated independently from candidate code.",
            "Sample counts conform strictly to configured epoch manifest thresholds.",
        ],
    },
    {
        "id": "creator_cohort",
        "code": "S",
        "label": "Creator Cohort",
        "subtitle": "Multi-Model Synthesis Agents",
        "category": "tournament_loop",
        "badge": 3,
        "x": 8,
        "y": 4,
        "z": 0,
        "width": 3.2,
        "depth": 3.2,
        "height": 2.2,
        "style": "crosshatch",
        "summary": "Independent creator agents (Gemini, Claude, Llama, Mistral) write novel benchmark suites.",
        "description": "Frontline creator models receive problem seeds and generate standalone benchmark packages containing task generators, reference oracles, and automated grading functions.",
        "invariants": [
            "Creators run in complete isolation with zero cross-creator communication during generation.",
            "Agent execution conforms to strict timeout and token consumption limits.",
            "Outputs must pass syntax checks before proceeding to the sandbox blast chamber.",
        ],
    },
    {
        "id": "sandbox_chamber",
        "code": "CS",
        "label": "Sandbox Blast Chamber",
        "subtitle": "Hermetic Code Isolation",
        "category": "tournament_loop",
        "badge": 2,
        "x": 13,
        "y": 4,
        "z": 0,
        "width": 3.6,
        "depth": 3.2,
        "height": 2.4,
        "style": "multi_pillar",
        "summary": "Executes untrusted candidate code and reference oracles inside isolated Linux namespaces.",
        "description": "All generated Python code runs inside a hardened containerized sandbox with kernel namespaces, memory bounds, process limits, and network isolation.",
        "invariants": [
            "Network access is unconditionally disabled (CLONE_NEWNET).",
            "Memory and CPU quotas are strictly enforced with SIGKILL termination on limits.",
            "Filesystem access is limited to a read-only root and ephemeral memory-backed temp workspace.",
        ],
    },
    {
        "id": "freeze_seal",
        "code": "F",
        "label": "Freeze Seal",
        "subtitle": "Merkle Tree Digest Locking",
        "category": "tournament_loop",
        "badge": 1,
        "x": 18,
        "y": 4.5,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.6,
        "style": "solid",
        "summary": "Computes SHA-256 tree digests and permanently locks candidate files into immutable evidence.",
        "description": "The Freeze Seal performs Merkle tree hashing across all candidate source files, freezing the candidate snapshot for downstream validation, solving, and human review.",
        "invariants": [
            "Any modification to candidate source files invalidates the snapshot tree digest.",
            "Frozen snapshots are atomically published to prevent partial or corrupted writes.",
            "Snapshots link backward to their parent round candidate for evolution tracing.",
        ],
    },
    {
        "id": "package_validator",
        "code": "V",
        "label": "Package Validator",
        "subtitle": "Oracle & Contract Verification",
        "category": "tournament_loop",
        "badge": 1,
        "x": 18,
        "y": 9.5,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.8,
        "style": "crosshatch",
        "summary": "Runs automated validation passes ensuring benchmarks are well-formed, solvable, and deterministically scorable.",
        "description": "The Package Validator exercises candidate oracles against synthesized task instances to ensure valid exit codes, correct output schemas, and non-empty score distributions.",
        "invariants": [
            "Candidates must pass 100% of baseline validation checks to be eligible for the solver panel.",
            "Broken or crash-prone benchmarks are marked invalid and prevented from burning solver budgets.",
            "Validation metrics and sandbox execution logs are captured for operator audit.",
        ],
    },
    {
        "id": "solver_panel",
        "code": "SB",
        "label": "Solver Panel",
        "subtitle": "Multi-Agent Evaluation Fleet",
        "category": "tournament_loop",
        "badge": 4,
        "x": 14,
        "y": 11,
        "z": 0,
        "width": 4.8,
        "depth": 4.0,
        "height": 2.4,
        "style": "hatched_grid",
        "summary": "Dispatches competing solver agents against validated benchmark candidates across multiple repetitions.",
        "description": "The Solver Panel subjects all candidate benchmarks to solver models from diverse providers, collecting multiple independent solution attempts to compute robust difficulty curves.",
        "invariants": [
            "Solvers have zero visibility into ground-truth reference oracles or hidden test instances.",
            "Solver attempts are recorded with complete prompt tokens, execution trace, and tool call metrics.",
            "Repetition counts match epoch manifest requirements (e.g. 3 repetitions per cell).",
        ],
    },
    {
        "id": "matrix_scorer",
        "code": "SC",
        "label": "Matrix Scorer",
        "subtitle": "Cross-Evaluation & Bradley-Terry",
        "category": "tournament_loop",
        "badge": 2,
        "x": 9.5,
        "y": 12.5,
        "z": 0,
        "width": 3.0,
        "depth": 3.0,
        "height": 2.0,
        "style": "crosshatch",
        "summary": "Assembles the creator-by-solver score matrix and computes calibrated Elo & Bradley-Terry rankings.",
        "description": "The Matrix Scorer aggregates solver accuracies across all benchmark candidates, computing non-parametric median scores, macro-accuracies, and 95% bootstrap confidence intervals.",
        "invariants": [
            "Creator rank rewards hard, discriminating, approved benchmarks.",
            "Solver rank uses balanced macro-averaging across canonical benchmarks.",
            "Confidence intervals are bootstrapped without parametric normality assumptions.",
        ],
    },
    {
        "id": "promotion_registry",
        "code": "PR",
        "label": "Promotion Registry",
        "subtitle": "Construct Validity & Signed Reviews",
        "category": "tournament_loop",
        "badge": 2,
        "x": 5.5,
        "y": 13,
        "z": 0,
        "width": 3.0,
        "depth": 3.0,
        "height": 1.8,
        "style": "solid",
        "summary": "Manages independent solvability certificates and cryptographic reviewer promotion signatures.",
        "description": "The Promotion Registry handles the human-in-the-loop review workflow. Expert reviewers verify construct validity against 7 rigorous findings and record Ed25519-signed promotion decisions.",
        "invariants": [
            "Approval requires passing all 7 construct validity findings.",
            "Approving reviewer ID must differ from the solvability certificate issuer.",
            "Signed reviews and certificates are permanently committed before sealed holdout audit.",
        ],
    },

    # SUPPORTING INFRASTRUCTURE
    {
        "id": "quota_governor",
        "code": "QG",
        "label": "Quota Governor",
        "subtitle": "Effective Model Rate Limiter",
        "category": "supporting_infrastructure",
        "badge": 2,
        "x": 13.5,
        "y": 1,
        "z": 0,
        "width": 2.6,
        "depth": 2.4,
        "height": 1.5,
        "style": "solid",
        "summary": "Tracks active Vertex AI token buckets and limits peak utilization to 70% of cloud quotas.",
        "description": "The Quota Governor coordinates concurrency across parallel creator and solver invocations, dynamically throttling traffic to prevent HTTP 429 quota exhaustion.",
        "invariants": [
            "Shared model quota buckets are tracked across all concurrent workers.",
            "Utilization is capped at configured target headroom (default 70%).",
            "Preflight verifies quota availability before paid tournament tasks begin.",
        ],
    },
    {
        "id": "session_budget",
        "code": "TB",
        "label": "Session Budget",
        "subtitle": "Hard Spend Ceiling & Telemetry",
        "category": "supporting_infrastructure",
        "badge": 1,
        "x": 18,
        "y": 1,
        "z": 0,
        "width": 2.6,
        "depth": 2.4,
        "height": 1.5,
        "style": "solid",
        "summary": "Monitors real-time token spend and enforces conservative dollar cost limits.",
        "description": "The Session Budget subsystem continuously aggregates input and output token counts against frozen model price catalog rates, applying safety multipliers to prevent budget overruns.",
        "invariants": [
            "Strict max spend ceiling (e.g. $500.00) halts execution immediately if projected cost exceeds limit.",
            "Pricing uses frozen, conservative cost catalogs with safety factors.",
            "Cumulative spend is persisted with every transaction.",
        ],
    },
    {
        "id": "sqlite_wal",
        "code": "SS",
        "label": "SQLite WAL Store",
        "subtitle": "Serialized State & Resumption",
        "category": "supporting_infrastructure",
        "badge": 1,
        "x": 2,
        "y": 16,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.4,
        "style": "layered",
        "summary": "Maintains ACID state transactions with Write-Ahead Logging for crash-safe recovery.",
        "description": "Local SQLite database in WAL mode stores execution status, task queues, and intermediate candidate states, enabling instantaneous resumption if the controller is interrupted.",
        "invariants": [
            "Serialized writer transactions eliminate concurrency corruption.",
            "WAL journal mode enables high-throughput non-blocking concurrent reads.",
            "Crash recovery restores exact pending work on next startup.",
        ],
    },
    {
        "id": "evidence_store",
        "code": "ES",
        "label": "Evidence Store",
        "subtitle": "Append-Only File Hierarchy",
        "category": "supporting_infrastructure",
        "badge": 2,
        "x": 5.8,
        "y": 17,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.4,
        "style": "layered",
        "summary": "Root evidence directory storing candidate source files, traces, logs, and cryptographic proofs.",
        "description": "All benchmark artifacts, solver attempts, test outputs, and signed reviews are stored in a clean, self-contained filesystem hierarchy suitable for one-click backup and audit.",
        "invariants": [
            "Atomic writes via temporary staging and hardlink/rename prevent race conditions.",
            "Directory layout strictly matches canonical BBA evidence schema.",
            "All operations stay on the local machine without unsolicited external cloud uploads.",
        ],
    },

    # MODELS & ENGINES
    {
        "id": "adk_runtime",
        "code": "ADK",
        "label": "Google ADK 2.6.3",
        "subtitle": "Agent Development Kit Core",
        "category": "models_and_engines",
        "badge": 3,
        "x": 10.5,
        "y": 17,
        "z": 0,
        "width": 3.0,
        "depth": 2.6,
        "height": 1.8,
        "style": "crosshatch",
        "summary": "Orchestrates multi-turn agent conversations, tool dispatching, and OpenTelemetry trace emission.",
        "description": "Google ADK powers agent execution, managing conversation loops, tool calling interfaces, structured parameter validation, and telemetry hooks.",
        "invariants": [
            "ADK telemetry captures invocation lifecycle without recording raw private prompts.",
            "Tool execution dispatches cleanly to secure sandbox runners.",
            "OTEL trace records are exported locally for debugging.",
        ],
    },
    {
        "id": "bubblewrap_sandbox",
        "code": "BW",
        "label": "Bubblewrap Sandbox",
        "subtitle": "Linux Namespace Isolation",
        "category": "models_and_engines",
        "badge": 1,
        "x": 15,
        "y": 17,
        "z": 0,
        "width": 2.6,
        "depth": 2.6,
        "height": 1.6,
        "style": "solid",
        "summary": "Kernel-level unshare isolation engine providing hermetic container boundaries.",
        "description": "Low-level Linux security substrate using unshare/bwrap to isolate process trees, mounts, and network interfaces during code execution.",
        "invariants": [
            "Privilege escalation is prevented with no-new-privs flags.",
            "Mount points are strictly read-only except for bounded private scratch storage.",
            "Clean exit cleanup ensures zero orphaned child processes.",
        ],
    },
    {
        "id": "multiprovider_fleet",
        "code": "MR",
        "label": "Multi-Provider Fleet",
        "subtitle": "Serverless Model Endpoints",
        "category": "models_and_engines",
        "badge": 3,
        "x": 19,
        "y": 16.5,
        "z": 0,
        "width": 3.2,
        "depth": 2.6,
        "height": 1.8,
        "style": "crosshatch",
        "summary": "Direct Google Cloud Vertex AI serverless routes across Google Gemini, Anthropic Claude, and xAI Grok model families.",
        "description": "Connects to official cloud provider endpoints with automatic retries, exponential backoff, and standardized schema translation.",
        "invariants": [
            "Inference stays on designated Google Cloud Vertex AI regions.",
            "Direct serverless routing requires zero third-party relay proxies.",
            "Authentication uses official Application Default Credentials (ADC).",
        ],
    },
]

DEFAULT_CIRCUITS = [
    # Primary Tournament Loop
    {
        "id": "loop_archive_to_creator",
        "name": "Seed & History Ingestion",
        "from": "archive_vault",
        "to": "creator_cohort",
        "points": [[4.9, 5.9, 0], [6.5, 5.9, 0], [6.5, 5.6, 0], [8.0, 5.6, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_creator_to_sandbox",
        "name": "Candidate Code Dispatch",
        "from": "creator_cohort",
        "to": "sandbox_chamber",
        "points": [[11.2, 5.6, 0], [13.0, 5.6, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_sandbox_to_freeze",
        "name": "Verified Candidate Seal",
        "from": "sandbox_chamber",
        "to": "freeze_seal",
        "points": [[16.6, 5.6, 0], [18.0, 5.6, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_freeze_to_validator",
        "name": "Snapshot Package Verification",
        "from": "freeze_seal",
        "to": "package_validator",
        "points": [[19.3, 7.1, 0], [19.3, 9.5, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_validator_to_solver",
        "name": "Benchmark Evaluation Dispatch",
        "from": "package_validator",
        "to": "solver_panel",
        "points": [[18.0, 11.5, 0], [16.4, 11.5, 0], [16.4, 12.0, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_solver_to_scorer",
        "name": "Solver Score Collection",
        "from": "solver_panel",
        "to": "matrix_scorer",
        "points": [[14.0, 13.0, 0], [12.5, 13.0, 0], [12.5, 13.5, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_scorer_to_promotion",
        "name": "Ranked Matrix Review",
        "from": "matrix_scorer",
        "to": "promotion_registry",
        "points": [[9.5, 14.0, 0], [8.5, 14.0, 0], [8.5, 14.5, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },
    {
        "id": "loop_promotion_to_archive",
        "name": "Promoted Suite Feedback",
        "from": "promotion_registry",
        "to": "archive_vault",
        "points": [[5.5, 14.0, 0], [3.5, 14.0, 0], [3.5, 7.8, 0], [4.9, 7.8, 0]],
        "loop": True,
        "active": True,
        "style": "solid",
    },

    # Supporting lines
    {
        "id": "support_budget_to_quota",
        "name": "Spend Ceiling Gate",
        "from": "session_budget",
        "to": "quota_governor",
        "points": [[18.0, 2.2, 0], [16.1, 2.2, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_quota_to_creators",
        "name": "Rate Limit Control",
        "from": "quota_governor",
        "to": "creator_cohort",
        "points": [[13.5, 2.2, 0], [9.6, 2.2, 0], [9.6, 4.0, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_seed_to_creators",
        "name": "Seed Feed",
        "from": "seed_foundry",
        "to": "creator_cohort",
        "points": [[4.3, 10.0, 0], [4.3, 8.0, 0], [8.0, 8.0, 0], [8.0, 6.5, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_wal_to_promotion",
        "name": "State Synchronization",
        "from": "sqlite_wal",
        "to": "promotion_registry",
        "points": [[3.3, 16.0, 0], [3.3, 15.0, 0], [5.5, 15.0, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_bwrap_to_sandbox",
        "name": "Namespace Chamber Feed",
        "from": "bubblewrap_sandbox",
        "to": "sandbox_chamber",
        "points": [[16.3, 17.0, 0], [16.3, 7.2, 0], [14.8, 7.2, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_adk_to_solver",
        "name": "ADK Tool Orchestration",
        "from": "adk_runtime",
        "to": "solver_panel",
        "points": [[12.0, 17.0, 0], [12.0, 15.0, 0], [14.0, 15.0, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
    {
        "id": "support_models_to_adk",
        "name": "Inference Stream",
        "from": "multiprovider_fleet",
        "to": "adk_runtime",
        "points": [[19.0, 17.8, 0], [13.5, 17.8, 0]],
        "loop": False,
        "active": True,
        "style": "dashed",
    },
]


class VisualizerStateSerializer:
    """Aggregates all BBA subsystem states into unified JSON payloads for the spatial UI."""

    @staticmethod
    def get_default_graph_layout() -> dict[str, Any]:
        return {
            "categories": DEFAULT_CATEGORIES,
            "nodes": DEFAULT_NODES,
            "circuits": DEFAULT_CIRCUITS,
        }

    @classmethod
    def serialize_system_state(cls, console: OperatorConsole) -> dict[str, Any]:
        """Aggregate workspace readiness, diagnostics, epoch overview, and job states."""
        try:
            readiness = console.readiness()
        except Exception as exc:
            readiness = {
                "ready": False,
                "checks": [{"id": "error", "label": "Readiness Error", "status": "failed", "detail": str(exc), "required": True}],
                "catalog_version": "unknown",
                "model_count": 0,
                "python": "unknown",
                "google_adk": "unknown",
                "evidence_root": str(getattr(console.evidence, "root", "")),
            }

        epochs = console.list_epochs()
        recent_jobs = console.jobs.recent(25)
        active_job = next((j for j in recent_jobs if j["status"] in {"queued", "running"}), None)
        layout = cls.get_default_graph_layout()

        return {
            "system": {
                "ready": readiness.get("ready", False),
                "checks": readiness.get("checks", []),
                "catalog_version": readiness.get("catalog_version", ""),
                "model_count": readiness.get("model_count", 0),
                "python": readiness.get("python", ""),
                "google_adk": readiness.get("google_adk", ""),
                "evidence_root": readiness.get("evidence_root", ""),
                "quota": readiness.get("quota"),
            },
            "epochs": epochs,
            "recent_jobs": recent_jobs,
            "active_job": active_job,
            "diagnostic_actions": getattr(console, "DIAGNOSTIC_ACTIONS", {}),
            "epoch_actions": getattr(console, "EPOCH_ACTIONS", {}),
            "graph": layout,
            "excalidraw": cls.to_excalidraw_scene(layout["nodes"], layout["circuits"], layout["categories"]),
        }

    @classmethod
    def serialize_epoch_state(cls, console: OperatorConsole, epoch_id: str) -> dict[str, Any]:
        """Aggregate rich state for an epoch including candidates, ranking matrices, observability, and telemetry."""
        try:
            epoch_data = console.epoch(epoch_id)
        except Exception as exc:
            try:
                manifest = console.evidence.load_manifest(epoch_id)
                status = console.state.status(epoch_id)
                epoch_data = {
                    "epoch_id": epoch_id,
                    "phase": status.get("phase", "frozen_historical"),
                    "manifest": {
                        "catalog_version": manifest.catalog_version,
                        "created_at": getattr(manifest, "created_at", None) or status.get("updated_at", "—"),
                        "gcp_project": manifest.gcp_project,
                        "gcp_location": manifest.gcp_location,
                        "models": len(manifest.cohort),
                        "rounds": manifest.thresholds.rounds,
                        "solver_repetitions": manifest.thresholds.solver_repetitions,
                    },
                    "candidates": [],
                    "approved": 0,
                    "error": str(exc),
                }
            except Exception:
                raise exc
        candidates_raw = epoch_data.get("candidates", [])
        
        try:
            results_data = console.results(epoch_id)
        except Exception:
            results_data = {"public": None, "audit": None}

        try:
            observability_data = console.observability(epoch_id)
        except Exception:
            observability_data = {"totals": {"invocations": 0, "model_calls": 0, "tool_calls": 0, "total_tokens": 0, "duration_ms": 0}, "models": [], "recent": [], "tracing": {}}

        recent_jobs = [j for j in console.jobs.recent(50) if j.get("epoch_id") == epoch_id or not j.get("epoch_id")]
        active_job = next((j for j in recent_jobs if j.get("epoch_id") == epoch_id and j["status"] in {"queued", "running"}), None)
        
        # Enrich candidates with basic preview info
        candidates = []
        for cand in candidates_raw:
            c_info = dict(cand)
            c_info["is_reviewed"] = bool(c_info.get("reviewed"))
            candidates.append(c_info)

        layout = cls.get_default_graph_layout()

        # Update node live status
        phase = epoch_data.get("phase", "created")
        for node in layout["nodes"]:
            nid = node["id"]
            if nid == "archive_vault":
                node["metric"] = f"{len(candidates)} snapshots"
                node["status"] = "active" if candidates else "idle"
            elif nid == "creator_cohort":
                node["metric"] = f"{epoch_data.get('manifest', {}).get('models', 0)} creators"
                node["status"] = "running" if phase in {"created", "public_running"} else "idle"
            elif nid == "sandbox_chamber":
                node["metric"] = f"{sum(1 for c in candidates if c.get('validation_passed'))} passed"
                node["status"] = "running" if phase == "public_running" else "idle"
            elif nid == "solver_panel":
                node["metric"] = f"{epoch_data.get('solver_cells', 0)} cells"
                node["status"] = "running" if phase == "public_running" else "idle"
            elif nid == "matrix_scorer":
                node["metric"] = "published" if results_data.get("public") else "pending"
                node["status"] = "active" if results_data.get("public") else "idle"
            elif nid == "promotion_registry":
                node["metric"] = f"{epoch_data.get('approved', 0)} approved"
                node["status"] = "active" if epoch_data.get("approved", 0) > 0 else "idle"
            elif nid == "session_budget":
                spend = epoch_data.get("usage", {}).get("estimated_cost_usd", 0.0)
                limit = epoch_data.get("max_estimated_cost_usd", 500.0) or 500.0
                node["metric"] = f"${spend:.2f} / ${limit:.0f}"
                node["status"] = "warning" if spend > limit * 0.8 else "active"

        return {
            "epoch_id": epoch_id,
            "manifest": epoch_data.get("manifest", {}),
            "phase": epoch_data.get("phase", "unknown"),
            "workflow": epoch_data.get("workflow", []),
            "action_states": epoch_data.get("action_states", {}),
            "usage": epoch_data.get("usage", {}),
            "snapshots_count": epoch_data.get("snapshots", 0),
            "solver_cells_count": epoch_data.get("solver_cells", 0),
            "approved_count": epoch_data.get("approved", 0),
            "review_open": epoch_data.get("review_open", False),
            "preflight_passed": epoch_data.get("preflight_passed", False),
            "failed_work": epoch_data.get("failed_work", []),
            "quota": epoch_data.get("quota"),
            "candidates": candidates,
            "results": results_data,
            "observability": observability_data,
            "active_job": active_job,
            "recent_jobs": recent_jobs[:10],
            "graph": layout,
            "excalidraw": cls.to_excalidraw_scene(layout["nodes"], layout["circuits"], layout["categories"], epoch_data),
            "finding_labels": {
                "named_capability_valid": "The named capability is valid.",
                "public_materials_sufficient": "The public materials are sufficient.",
                "oracle_consistent": "The oracle is consistent.",
                "scorer_consistent": "The scorer is consistent.",
                "no_arbitrary_obscurity": "The benchmark has no arbitrary obscurity.",
                "useful_evaluation": "The benchmark is a useful evaluation.",
                "solvability_certificate_adequate": "The solvability certificate is adequate.",
            },
            "certificate_types": [item.value for item in SolvabilityCertificateType],
            "promotion_decisions": [item.value for item in PromotionDecision],
        }

    @classmethod
    def to_excalidraw_scene(
        cls,
        nodes: list[dict[str, Any]],
        circuits: list[dict[str, Any]],
        categories: list[dict[str, Any]],
        epoch_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Convert BBA architectural graph into high-fidelity Excalidraw visual argument model."""
        elements: list[dict[str, Any]] = []
        seed_counter = 1000

        def next_seed() -> int:
            nonlocal seed_counter
            seed_counter += 1
            return seed_counter

        def add_text_element(
            eid: str,
            x: float,
            y: float,
            w: float,
            h: float,
            text: str,
            font_size: float = 12,
            font_family: int = 3,
            color: str = "#1e1d18",
            align: str = "center",
            valign: str = "middle",
            locked: bool = False,
            groups: list | None = None,
            custom_data: dict | None = None,
            auto_resize: bool = False,
        ) -> None:
            lines = text.split("\n")
            max_line_len = max((len(line) for line in lines), default=0)
            line_count = max(len(lines), 1)

            # Monospace characters: allow 0.78 * font_size per character + 50px margin
            char_w = font_size * 0.78
            measured_w = max_line_len * char_w + 50
            measured_h = line_count * font_size * 1.45 + 10

            final_w = max(w, measured_w)
            final_h = max(h, measured_h)

            elements.append({
                "id": eid,
                "type": "text",
                "x": x,
                "y": y,
                "width": final_w,
                "height": final_h,
                "angle": 0,
                "strokeColor": color,
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "groupIds": groups or [],
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "text": text,
                "originalText": text,
                "fontSize": font_size,
                "fontFamily": font_family,
                "textAlign": align,
                "verticalAlign": valign,
                "lineHeight": 1.25,
                "containerId": None,
                "autoResize": False,
                "locked": locked,
                "customData": custom_data or {},
            })

        # --- LEVEL 1: SUMMARY FLOW ---
        # Summary timeline flow steps (properly spaced)
        summary_steps = [
            ("01. Seed", 60),
            ("02. Synthesize", 220),
            ("03. Blast Box", 400),
            ("04. Merkle Seal", 580),
            ("05. Solve Arena", 770),
            ("06. Score Matrix", 960),
            ("07. Verify & Sign", 1150),
            ("08. Canonical Suite", 1340),
        ]
        for idx, (label, sx) in enumerate(summary_steps):
            elements.append({
                "id": f"sum_dot_{idx}",
                "type": "ellipse",
                "x": sx,
                "y": 30,
                "width": 12,
                "height": 12,
                "angle": 0,
                "strokeColor": "#1d4ed8" if idx == 0 else ("#15803d" if idx == 7 else "#b45309"),
                "backgroundColor": "#dbeafe" if idx == 0 else ("#dcfce7" if idx == 7 else "#fef3c7"),
                "fillStyle": "solid",
                "strokeWidth": 1.5,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "groupIds": [],
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "locked": True,
            })
            add_text_element(
                f"sum_label_{idx}",
                x=sx + 18, y=28, w=140, h=20,
                text=label,
                font_size=10.5, font_family=3, color="#1f2937", align="left", valign="middle",
            )
            if idx < len(summary_steps) - 1:
                next_sx = summary_steps[idx + 1][1]
                arrow_w = max(10, next_sx - sx - 155)
                elements.append({
                    "id": f"sum_arrow_{idx}",
                    "type": "arrow",
                    "x": sx + 145,
                    "y": 36,
                    "width": arrow_w,
                    "height": 0,
                    "angle": 0,
                    "strokeColor": "#9ca3af",
                    "backgroundColor": "transparent",
                    "fillStyle": "solid",
                    "strokeWidth": 1,
                    "strokeStyle": "solid",
                    "roughness": 0,
                    "opacity": 80,
                    "groupIds": [],
                    "seed": next_seed(),
                    "version": 1,
                    "versionNonce": 1,
                    "isDeleted": False,
                    "points": [[0, 0], [arrow_w, 0]],
                    "endArrowhead": "arrow",
                    "locked": True,
                })

        # --- LEVEL 2: REGIONAL SECTION BOUNDARIES ---
        sections = [
            {
                "id": "sec_tournament",
                "title": "[SECTION 1: ADVERSARIAL TOURNAMENT PIPELINE]",
                "x": 60,
                "y": 75,
                "width": 1180,
                "height": 540,
                "strokeColor": "#d97706",
                "backgroundColor": "transparent",
            },
            {
                "id": "sec_governance",
                "title": "[SECTION 2: GOVERNANCE & STATE]",
                "x": 1270,
                "y": 75,
                "width": 490,
                "height": 265,
                "strokeColor": "#6b7280",
                "backgroundColor": "transparent",
            },
            {
                "id": "sec_isolation",
                "title": "[SECTION 3: HERMETIC ISOLATION & MODEL FLEET]",
                "x": 1270,
                "y": 360,
                "width": 490,
                "height": 265,
                "strokeColor": "#6b7280",
                "backgroundColor": "transparent",
            },
            {
                "id": "sec_leaderboard",
                "title": "[SECTION 4: BRADLEY-TERRY ELO LEADERBOARD & EVALUATION STATUS]",
                "x": 60,
                "y": 640,
                "width": 1700,
                "height": 360,
                "strokeColor": "#2563eb",
                "backgroundColor": "transparent",
            },
        ]

        for sec in sections:
            elements.append({
                "id": f"bound_{sec['id']}",
                "type": "rectangle",
                "x": sec["x"],
                "y": sec["y"],
                "width": sec["width"],
                "height": sec["height"],
                "angle": 0,
                "strokeColor": sec["strokeColor"],
                "backgroundColor": sec["backgroundColor"],
                "fillStyle": "solid",
                "strokeWidth": 1.5,
                "strokeStyle": "dashed",
                "roughness": 0,
                "opacity": 60,
                "groupIds": [],
                "roundness": None,
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "locked": True,
            })
            add_text_element(
                f"label_{sec['id']}",
                x=sec["x"] + 16, y=sec["y"] + 12, w=len(sec["title"]) * 8, h=18,
                text=sec["title"],
                font_size=12, font_family=3, color=sec["strokeColor"], align="left", valign="top",
            )

        # --- LEVEL 3: NODES (PRECISION GRID COORDINATES) ---
        node_positions = {
            # Section 1: Tournament Pipeline (Row 1)
            "seed_foundry": {"x": 100, "y": 140, "w": 230, "h": 85},
            "creator_cohort": {"x": 390, "y": 140, "w": 240, "h": 85},
            "sandbox_chamber": {"x": 690, "y": 140, "w": 240, "h": 85},
            "freeze_seal": {"x": 990, "y": 140, "w": 220, "h": 85},
            # Section 1: Tournament Pipeline (Row 2)
            "archive_vault": {"x": 100, "y": 275, "w": 230, "h": 85},
            "package_validator": {"x": 990, "y": 275, "w": 220, "h": 85},
            # Section 1: Tournament Pipeline (Row 3)
            "promotion_registry": {"x": 100, "y": 410, "w": 230, "h": 85},
            "matrix_scorer": {"x": 390, "y": 410, "w": 240, "h": 85},
            "solver_panel": {"x": 690, "y": 410, "w": 240, "h": 85},
            # Section 2: Governance & State
            "session_budget": {"x": 1300, "y": 130, "w": 205, "h": 80},
            "quota_governor": {"x": 1535, "y": 130, "w": 205, "h": 80},
            "sqlite_wal": {"x": 1300, "y": 235, "w": 205, "h": 80},
            "evidence_store": {"x": 1535, "y": 235, "w": 205, "h": 80},
            # Section 3: Hermetic Isolation & Fleet
            "bubblewrap_sandbox": {"x": 1300, "y": 405, "w": 205, "h": 80},
            "adk_runtime": {"x": 1535, "y": 405, "w": 205, "h": 80},
            "multiprovider_fleet": {"x": 1300, "y": 515, "w": 440, "h": 80},
        }

        # Render Nodes
        for node in nodes:
            nid = node["id"]
            pos = node_positions.get(nid, {"x": node["x"] * 65, "y": node["y"] * 55, "w": 240, "h": 85})
            status = node.get("status", "idle")

            if status == "running":
                bg, stroke = "#dbeafe", "#1d4ed8"
            elif status == "active":
                bg, stroke = "#dcfce7", "#15803d"
            elif status == "warning":
                bg, stroke = "#fef3c7", "#b45309"
            else:
                bg, stroke = "#ffffff", "#1e1d18"

            # Main Node Container
            elements.append({
                "id": f"box_{nid}",
                "type": "rectangle",
                "x": pos["x"],
                "y": pos["y"],
                "width": pos["w"],
                "height": pos["h"],
                "angle": 0,
                "strokeColor": stroke,
                "backgroundColor": bg,
                "fillStyle": "solid",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "groupIds": [f"grp_{nid}"],
                "roundness": None,
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "customData": {"nodeId": nid, "type": "node"},
            })

            # Node Title (Clean, left-aligned, ample width)
            add_text_element(
                f"txt_label_{nid}",
                x=pos["x"] + 12, y=pos["y"] + 12, w=pos["w"] - 24, h=20,
                text=node["label"],
                font_size=12, font_family=3, color="#111827", align="left", valign="top",
                locked=False, groups=[f"grp_{nid}"], custom_data={"nodeId": nid, "type": "node"},
            )

            # Subtitle
            add_text_element(
                f"txt_sub_{nid}",
                x=pos["x"] + 12, y=pos["y"] + 36, w=pos["w"] - 24, h=24,
                text=node.get("subtitle", ""),
                font_size=9.5, font_family=3, color="#4b5563", align="left", valign="top",
                locked=False, groups=[f"grp_{nid}"], custom_data={"nodeId": nid, "type": "node"},
            )

            # Metric if active
            if node.get("metric"):
                metric_color = "#b45309" if status == "warning" else ("#15803d" if status == "active" else "#1d4ed8")
                add_text_element(
                    f"txt_metric_{nid}",
                    x=pos["x"] + 12, y=pos["y"] + pos["h"] - 20, w=pos["w"] - 24, h=16,
                    text=f"● {node['metric']}",
                    font_size=9, font_family=3, color=metric_color, align="left", valign="top",
                    locked=False, groups=[f"grp_{nid}"], custom_data={"nodeId": nid, "type": "node"},
                )

        # --- LEVEL 3: STRUCTURED REAL TABLE: BRADLEY-TERRY ELO RANKING ---
        tbl_x = 90
        tbl_y = 685
        tbl_w = 780
        tbl_h = 295

        # Outer Table Card
        elements.append({
            "id": "box_ev_ranked_matrix",
            "type": "rectangle",
            "x": tbl_x,
            "y": tbl_y,
            "width": tbl_w,
            "height": tbl_h,
            "angle": 0,
            "strokeColor": "#d1d5db",
            "backgroundColor": "#ffffff",
            "fillStyle": "solid",
            "strokeWidth": 1.5,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": ["grp_ev_ranked_matrix"],
            "roundness": None,
            "seed": next_seed(),
            "version": 1,
            "versionNonce": 1,
            "isDeleted": False,
            "boundElements": [],
            "locked": True,
            "customData": {"nodeId": "matrix_scorer", "type": "table"},
        })

        # Table Header Banner
        elements.append({
            "id": "hdr_banner_ranked_matrix",
            "type": "rectangle",
            "x": tbl_x,
            "y": tbl_y,
            "width": tbl_w,
            "height": 26,
            "angle": 0,
            "strokeColor": "#e5e7eb",
            "backgroundColor": "#f3f4f6",
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": ["grp_ev_ranked_matrix"],
            "roundness": None,
            "seed": next_seed(),
            "version": 1,
            "versionNonce": 1,
            "isDeleted": False,
            "boundElements": [],
            "locked": True,
        })
        add_text_element(
            "hdr_txt_ranked_matrix",
            x=tbl_x + 10, y=tbl_y + 6, w=tbl_w - 20, h=16,
            text="Bradley-Terry Elo Ranking · Active Multi-Model Evaluation Fleet",
            font_size=10.5, font_family=3, color="#111827", align="left", valign="top",
            locked=True, groups=["grp_ev_ranked_matrix"],
        )

        # Column Header Row (y = tbl_y + 26, h = 20)
        elements.append({
            "id": "col_hdr_ranked_matrix",
            "type": "rectangle",
            "x": tbl_x,
            "y": tbl_y + 26,
            "width": tbl_w,
            "height": 20,
            "angle": 0,
            "strokeColor": "#e5e7eb",
            "backgroundColor": "#f9fafb",
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": ["grp_ev_ranked_matrix"],
            "seed": next_seed(),
            "version": 1,
            "versionNonce": 1,
            "isDeleted": False,
            "boundElements": [],
            "locked": True,
        })
        add_text_element("th_rank", x=tbl_x + 10, y=tbl_y + 29, w=35, h=14, text="RNK", font_size=8.5, font_family=3, color="#4b5563", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
        add_text_element("th_model", x=tbl_x + 55, y=tbl_y + 29, w=230, h=14, text="COHORT MODEL", font_size=8.5, font_family=3, color="#4b5563", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
        add_text_element("th_prov", x=tbl_x + 300, y=tbl_y + 29, w=120, h=14, text="PROVIDER", font_size=8.5, font_family=3, color="#4b5563", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
        add_text_element("th_acc", x=tbl_x + 440, y=tbl_y + 29, w=70, h=14, text="ACCURACY", font_size=8.5, font_family=3, color="#4b5563", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
        add_text_element("th_elo", x=tbl_x + 530, y=tbl_y + 29, w=140, h=14, text="CALIBRATED ELO", font_size=8.5, font_family=3, color="#4b5563", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
        add_text_element("th_status", x=tbl_x + 690, y=tbl_y + 29, w=75, h=14, text="STATUS", font_size=8.5, font_family=3, color="#4b5563", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])

        # Table Rows for all 9 Cohort Models
        cohort_rows = [
            ("01", "gemini-3.6-flash", "Google Vertex AI"),
            ("02", "claude-opus-5", "Anthropic Claude"),
            ("03", "claude-sonnet-5", "Anthropic Claude"),
            ("04", "grok-4.3", "xAI API"),
            ("05", "claude-opus-4-8", "Anthropic Claude"),
            ("06", "gemini-3.1-pro", "Google Vertex AI"),
            ("07", "claude-opus-4-7", "Anthropic Claude"),
            ("08", "gemini-3.5-flash", "Google Vertex AI"),
            ("09", "gemini-3.5-lite", "Google Vertex AI"),
        ]

        row_y = tbl_y + 46
        row_h = 24
        for idx, (rnk, model_name, prov) in enumerate(cohort_rows):
            cur_y = row_y + idx * row_h
            row_bg = "#ffffff" if idx % 2 == 0 else "#f9fafb"

            elements.append({
                "id": f"row_bg_{idx}",
                "type": "rectangle",
                "x": tbl_x,
                "y": cur_y,
                "width": tbl_w,
                "height": row_h,
                "angle": 0,
                "strokeColor": "#f3f4f6",
                "backgroundColor": row_bg,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "groupIds": ["grp_ev_ranked_matrix"],
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "locked": True,
            })
            add_text_element(f"td_rnk_{idx}", x=tbl_x + 10, y=cur_y + 5, w=35, h=14, text=rnk, font_size=8.5, font_family=3, color="#6b7280", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
            add_text_element(f"td_model_{idx}", x=tbl_x + 55, y=cur_y + 5, w=230, h=14, text=model_name, font_size=8.5, font_family=3, color="#111827", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
            add_text_element(f"td_prov_{idx}", x=tbl_x + 300, y=cur_y + 5, w=120, h=14, text=prov, font_size=8.5, font_family=3, color="#4b5563", align="left", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
            add_text_element(f"td_acc_{idx}", x=tbl_x + 440, y=cur_y + 5, w=70, h=14, text="—", font_size=8.5, font_family=3, color="#9ca3af", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
            add_text_element(f"td_elo_{idx}", x=tbl_x + 530, y=cur_y + 5, w=140, h=14, text="—", font_size=8.5, font_family=3, color="#9ca3af", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])
            add_text_element(f"td_status_{idx}", x=tbl_x + 690, y=cur_y + 5, w=75, h=14, text="Pending", font_size=8.5, font_family=3, color="#6b7280", align="center", valign="top", locked=True, groups=["grp_ev_ranked_matrix"])

        # Table Footer
        footer_y = row_y + len(cohort_rows) * row_h
        add_text_element(
            "tbl_footer_ranked_matrix",
            x=tbl_x + 10, y=footer_y + 5, w=tbl_w - 20, h=14,
            text="[Status: Awaiting Tournament Execution · Bradley-Terry Median Scoring with 95% Bootstrap CI]",
            font_size=8.5, font_family=3, color="#9ca3af", align="center", valign="top",
            locked=True, groups=["grp_ev_ranked_matrix"],
        )

        # --- LEVEL 3: CONSTRUCT VALIDITY & AUDIT PANEL (RIGHT SIDE OF SECTION 4) ---
        aud_x = 900
        aud_y = 685
        aud_w = 830
        aud_h = 295

        elements.append({
            "id": "box_audit_panel",
            "type": "rectangle",
            "x": aud_x,
            "y": aud_y,
            "width": aud_w,
            "height": aud_h,
            "angle": 0,
            "strokeColor": "#d1d5db",
            "backgroundColor": "#ffffff",
            "fillStyle": "solid",
            "strokeWidth": 1.5,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": ["grp_audit_panel"],
            "roundness": None,
            "seed": next_seed(),
            "version": 1,
            "versionNonce": 1,
            "isDeleted": False,
            "boundElements": [],
            "locked": True,
            "customData": {"nodeId": "promotion_registry", "type": "panel"},
        })

        # Header Banner
        elements.append({
            "id": "hdr_banner_audit_panel",
            "type": "rectangle",
            "x": aud_x,
            "y": aud_y,
            "width": aud_w,
            "height": 26,
            "angle": 0,
            "strokeColor": "#e5e7eb",
            "backgroundColor": "#f3f4f6",
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": ["grp_audit_panel"],
            "roundness": None,
            "seed": next_seed(),
            "version": 1,
            "versionNonce": 1,
            "isDeleted": False,
            "boundElements": [],
            "locked": True,
        })
        add_text_element(
            "hdr_txt_audit_panel",
            x=aud_x + 10, y=aud_y + 6, w=aud_w - 20, h=16,
            text="Construct Validity Invariants & Cryptographic Promotion Manifest",
            font_size=10.5, font_family=3, color="#111827", align="left", valign="top",
            locked=True, groups=["grp_audit_panel"],
        )

        # Audit Body Content (2 Columns)
        add_text_element(
            "txt_audit_col1",
            x=aud_x + 16, y=aud_y + 36, w=380, h=240,
            text="7 CONSTRUCT VALIDITY CHECKS:\n✓ 01. Non-Trivial Ground Truth Verifier\n✓ 02. Zero Benchmark Information Leakage\n✓ 03. Edge-Case Boundary Space Coverage\n✓ 04. Strict Decimal Half-Up Arithmetic\n✓ 05. Deterministic Random Seed Replay\n✓ 06. Hermetic Bubblewrap Sandbox Safe\n✓ 07. Positive Solver Discrimination Delta",
            font_size=9.5, font_family=3, color="#15803d", align="left", valign="top",
            locked=True, groups=["grp_audit_panel"],
        )

        add_text_element(
            "txt_audit_col2",
            x=aud_x + 420, y=aud_y + 36, w=380, h=240,
            text="PROMOTION SECURITY MANIFEST:\n• Solvability Certificate: Ed25519 Verified\n• Cryptographic Merkle Root: SHA-256 Sealed\n• Separation of Duties: Reviewer != Creator\n• Sealed Holdout Split: 20% Unseen Eval\n• State Journal: SQLite WAL Mode Committed\n• Storage Hierarchy: Append-Only Immutable\n• Catalog: Canonical Suite v1.0 Ready",
            font_size=9.5, font_family=3, color="#1f2937", align="left", valign="top",
            locked=True, groups=["grp_audit_panel"],
        )

        # --- FLOW ARROWS & CIRCUITS (ROUTED STRICTLY OUTSIDE ALL COMPONENTS) ---
        flow_arrows = [
            # 1. Seed Ingestion: seed_foundry (330, 182.5) -> creator_cohort (390, 182.5)
            {
                "id": "arr_seed_to_creator",
                "x": 330, "y": 182.5,
                "points": [[0, 0], [60, 0]],
                "color": "#1d4ed8", "style": "solid",
            },
            # 2. Candidate Code Dispatch: creator_cohort (630, 182.5) -> sandbox_chamber (690, 182.5)
            {
                "id": "arr_creator_to_sandbox",
                "x": 630, "y": 182.5,
                "points": [[0, 0], [60, 0]],
                "color": "#b45309", "style": "solid",
            },
            # 3. Verified Code: sandbox_chamber (930, 182.5) -> freeze_seal (990, 182.5)
            {
                "id": "arr_sandbox_to_freeze",
                "x": 930, "y": 182.5,
                "points": [[0, 0], [60, 0]],
                "color": "#15803d", "style": "solid",
            },
            # 4. Merkle Root Hash: freeze_seal (1100, 225) -> package_validator (1100, 275)
            {
                "id": "arr_freeze_to_validator",
                "x": 1100, "y": 225,
                "points": [[0, 0], [0, 50]],
                "color": "#4338ca", "style": "solid",
            },
            # 5. Benchmark Distribution: package_validator (1100, 360) -> solver_panel (810, 410)
            # Routes through open horizontal corridor at y=385
            {
                "id": "arr_validator_to_solver",
                "x": 1100, "y": 360,
                "points": [[0, 0], [0, 25], [-290, 25], [-290, 50]],
                "color": "#b45309", "style": "solid",
            },
            # 6. Raw Evaluation Scores: solver_panel (690, 452.5) -> matrix_scorer (630, 452.5)
            {
                "id": "arr_solver_to_scorer",
                "x": 690, "y": 452.5,
                "points": [[0, 0], [-60, 0]],
                "color": "#1d4ed8", "style": "solid",
            },
            # 7. Ranked Matrix Review: matrix_scorer (390, 452.5) -> promotion_registry (330, 452.5)
            {
                "id": "arr_scorer_to_promotion",
                "x": 390, "y": 452.5,
                "points": [[0, 0], [-60, 0]],
                "color": "#15803d", "style": "solid",
            },
            # 8. Feedback Loop: promotion_registry (215, 410) -> archive_vault (215, 360)
            {
                "id": "arr_promotion_to_archive",
                "x": 215, "y": 410,
                "points": [[0, 0], [0, -50]],
                "color": "#15803d", "style": "solid",
            },
            # 9. Archive to Seed: archive_vault (215, 275) -> seed_foundry (215, 225)
            {
                "id": "arr_archive_to_seed",
                "x": 215, "y": 275,
                "points": [[0, 0], [0, -50]],
                "color": "#1d4ed8", "style": "solid",
            },
            
            # Supporting Governance & Runtime Cross-Section Links
            # 10. Quota Governor (Section 2) -> Multi-Provider Fleet (Section 3)
            # Routes through open inter-column corridor at x=1520 (never touches evidence_store or adk_runtime)
            {
                "id": "arr_quota_to_fleet",
                "x": 1535, "y": 170,
                "points": [[0, 0], [-15, 0], [-15, 345]],
                "color": "#b45309", "style": "dashed",
            },
            # 11. Bubblewrap Sandbox (Section 3) -> Sandbox Blast Chamber (Section 1)
            # Routes around freeze_seal via the central vertical gutter (x=1245) and top open corridor (y=105)
            {
                "id": "arr_bwrap_to_sandbox",
                "x": 1300, "y": 445,
                "points": [[0, 0], [-55, 0], [-55, -340], [-490, -340], [-490, -305]],
                "color": "#4b5563", "style": "dashed",
            },
            # 12. Fleet to ADK Runtime (Inside Section 3)
            {
                "id": "arr_fleet_to_adk",
                "x": 1637.5, "y": 515,
                "points": [[0, 0], [0, -30]],
                "color": "#7e22ce", "style": "solid",
            },
        ]

        for arr in flow_arrows:
            xs = [pt[0] for pt in arr["points"]]
            ys = [pt[1] for pt in arr["points"]]
            w = max(abs(max(xs) - min(xs)), 1)
            h = max(abs(max(ys) - min(ys)), 1)

            elements.append({
                "id": arr["id"],
                "type": "arrow",
                "x": arr["x"],
                "y": arr["y"],
                "width": w,
                "height": h,
                "angle": 0,
                "strokeColor": arr["color"],
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 2 if arr["style"] == "solid" else 1.5,
                "strokeStyle": arr["style"],
                "roughness": 0,
                "opacity": 85,
                "groupIds": [],
                "roundness": None,
                "seed": next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "points": arr["points"],
                "endArrowhead": "arrow",
                "customData": {"type": "circuit"},
            })
            if arr.get("label"):
                lbl_text = arr["label"]
                calc_w = max(len(lbl_text) * 10 + 30, 90)
                calc_h = 20
                center_x = arr.get("lbl_x", min(xs) + w / 2)
                center_y = arr.get("lbl_y", min(ys) + h / 2)

                add_text_element(
                    f"lbl_{arr['id']}",
                    x=center_x - calc_w / 2,
                    y=center_y - calc_h / 2,
                    w=calc_w,
                    h=calc_h,
                    text=lbl_text,
                    font_size=10,
                    font_family=3,
                    color=arr["color"],
                    align="center",
                    valign="middle",
                    locked=True,
                )

        return {
            "type": "excalidraw",
            "version": 2,
            "source": "https://excalidraw.com",
            "elements": elements,
            "appState": {
                "viewBackgroundColor": "#f8f5ee",
                "gridModeEnabled": True,
                "zenModeEnabled": False,
                "viewModeEnabled": False,
            },
            "files": {},
        }

    @classmethod
    def serialize_candidate_details(
        cls, console: OperatorConsole, epoch_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        """Aggregate in-depth candidate details including file tree, code previews, and logs."""
        cand = console.candidate(epoch_id, snapshot_id)
        root = console.evidence.epoch_root(epoch_id)
        
        # Discover files in design directory
        files: list[dict[str, Any]] = []
        design_dir = root / "candidates" / snapshot_id / "design"
        if design_dir.is_dir():
            for path in sorted(design_dir.rglob("*")):
                if path.is_file() and not path.name.startswith("."):
                    rel = path.relative_to(design_dir).as_posix()
                    try:
                        size = path.stat().st_size
                        if size < 65536:
                            try:
                                content = path.read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                content = "<binary content>"
                        else:
                            content = f"<file size {size} bytes exceeds preview threshold>"
                        files.append({
                            "path": rel,
                            "name": path.name,
                            "size": size,
                            "content": content,
                        })
                    except Exception as e:
                        files.append({"path": rel, "name": path.name, "size": 0, "content": f"<read error: {e}>"})

        # Discover sandbox validation record
        validation_record: Optional[dict[str, Any]] = None
        val_file = root / "validations" / f"{snapshot_id}.json"
        if val_file.is_file():
            try:
                validation_record = json.loads(val_file.read_text(encoding="utf-8"))
            except Exception:
                validation_record = None

        return {
            "candidate": cand,
            "files": files,
            "validation": validation_record,
        }

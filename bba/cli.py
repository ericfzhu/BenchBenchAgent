"""Local command-line control for BBA validation and evaluation epochs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from bba.adk_runtime import build_adk_backends, build_hidden_solver_backends
from bba.audit_runner import SealedAuditRunner, build_public_audit_population
from bba.budget import estimate_epoch
from bba.catalog import catalog_summary
from bba.evidence import EvidenceStore, read_json, tree_digest
from bba.epoch_setup import create_experiment_manifest, new_epoch_id
from bba.gcp import discover_gcp_project
from bba.holdouts import HoldoutRegistry
from bba.protocol import (
    PromotionDecision,
    ReviewFindings,
    to_primitive,
)
from bba.preflight import run_preflight
from bba.replay import replay_solver_attempt
from bba.runtime import SecureSandbox
from bba.state import LocalStateStore, local_file_lock
from bba.tournament import TournamentController
from bba.validator import PackageValidator


def _print_json(value: Any) -> None:
    print(json.dumps(to_primitive(value), indent=2, sort_keys=True))


def _verify_package(args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    validator = PackageValidator(sandbox, sample_count=args.sample_count, timeout_seconds=args.timeout)
    package = Path(args.package).resolve()
    digest = tree_digest(package)
    record = validator.validate(package, "standalone", digest, args.seed)
    _print_json(record)
    return 0 if record.passed else 1


def _sandbox_status(_args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    _print_json({
        "backend": sandbox.backend,
        "available": sandbox.available,
        "unavailable_reason": sandbox.unavailable_reason or None,
        "fail_closed": True,
    })
    return 0 if sandbox.available else 1


def _evidence_replay_cell(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    _print_json(replay_solver_attempt(evidence, args.epoch_id, args.attempt_id))
    return 0


def _evidence(args: argparse.Namespace) -> EvidenceStore:
    return EvidenceStore(Path(args.evidence_root))


def _state(evidence: EvidenceStore) -> LocalStateStore:
    return LocalStateStore(evidence.root / "bba-state.sqlite3")


def _load_controller(
    evidence: EvidenceStore,
    epoch_id: str,
    state: LocalStateStore,
) -> TournamentController:
    return TournamentController(evidence.load_manifest(epoch_id), evidence, state=state)


def _saved_status(
    evidence: EvidenceStore,
    epoch_id: str,
    state: LocalStateStore,
) -> dict[str, Any]:
    root = evidence.epoch_root(epoch_id)
    result = state.status(epoch_id)
    result.update({
        "snapshots": sum(
            1
            for path in (root / "candidates").glob("*/snapshot.json")
            if not path.parent.name.startswith(".")
        ),
        "instances": sum(
            1
            for path in (root / "instances").glob("*/instance.json")
            if not path.parent.name.startswith(".")
        ),
        "validations": len(list((root / "validations").glob("*.json"))),
        "solver_cells": len(list((root / "solver-cells").glob("*.json"))),
        "promotions": len(list((root / "promotions").glob("*.json"))),
        "public_closed": (root / "evaluation" / "public.json").is_file(),
        "holdout_complete": (root / "audit" / "holdout.json").is_file(),
    })
    return result


def _epoch_create(args: argparse.Namespace) -> int:
    project = discover_gcp_project()
    epoch_id = args.epoch_id or new_epoch_id()
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{epoch_id}"):
        manifest_path = evidence.epoch_root(epoch_id) / "manifest.json"
        if manifest_path.is_file():
            manifest = evidence.load_manifest(epoch_id)
            if manifest.gcp_project != project:
                raise RuntimeError(
                    "the existing epoch belongs to a different GCP project"
                )
            private_path = (
                evidence.epoch_root(epoch_id) / "private" / "holdout-plan.json"
            )
            if not private_path.is_file():
                raise RuntimeError(f"epoch setup is incomplete: {epoch_id}")
        else:
            manifest, private = create_experiment_manifest(project, epoch_id=epoch_id)
            HoldoutRegistry(evidence).transition(
                epoch_id, manifest.hidden_commitments, "committed"
            )
            evidence.freeze_epoch_setup(manifest, private)
        controller = TournamentController(manifest, evidence, state=_state(evidence))
        _print_json(controller.epoch_status())
    return 0


def _catalog(_args: argparse.Namespace) -> int:
    _print_json(catalog_summary())
    return 0


def _epoch_run(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        manifest = evidence.load_manifest(args.epoch_id)
        preflight_path = evidence.record_path(args.epoch_id, "preflight", "vertex")
        if not preflight_path.is_file():
            raise RuntimeError(
                "paid epoch requires a passing Vertex preflight; run bba epoch preflight first"
            )
        state = _state(evidence)
        state.register_epoch(manifest)
        state.recover_interrupted(args.epoch_id)
        restored = TournamentController(manifest, evidence, state=state)
        if restored.epoch_status()["phase"] in {
            "awaiting_review",
            "audit_population_frozen",
            "public_closed",
            "audited",
        }:
            _print_json(restored.epoch_status())
            return 0
        sandbox = SecureSandbox(
            memory_mb=manifest.budget.memory_mb,
            process_limit=manifest.budget.process_limit,
            cpu_seconds=manifest.budget.cpu_seconds,
        )
        if not sandbox.available:
            raise RuntimeError(sandbox.unavailable_reason or "secure local sandbox is unavailable")
        creators, solvers = build_adk_backends(
            manifest,
            construction_sandbox=sandbox,
        )
        validator = PackageValidator(
            sandbox,
            sample_count=manifest.thresholds.sample_count,
            timeout_seconds=min(
                manifest.budget.creator_seconds,
                manifest.budget.solver_seconds,
            ),
        )
        controller = TournamentController(
            manifest,
            evidence,
            validator,
            creators,
            solvers,
            state,
        )
        controller.run_public_epoch()
        _print_json(controller.epoch_status())
    return 0


def _epoch_preflight(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        manifest = evidence.load_manifest(args.epoch_id)
        _print_json({
            "estimate": estimate_epoch(manifest),
            "preflight": run_preflight(manifest, evidence),
        })
    return 0


def _epoch_status(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    evidence.load_manifest(args.epoch_id)
    _print_json(_saved_status(evidence, args.epoch_id, _state(evidence)))
    return 0


def _epoch_review_items(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        snapshot = controller.snapshot_by_id(args.snapshot_id)
        _print_json({
            "snapshot_id": snapshot.snapshot_id,
            "item_ids": controller.select_review_items(snapshot),
        })
    return 0


def _epoch_candidates(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        _print_json([
            {
                "snapshot_id": snapshot.snapshot_id,
                "creator": snapshot.creator.artifact_id,
                "round": snapshot.round_index,
                "design_digest": snapshot.design_digest,
                "parent_snapshot_id": snapshot.parent_snapshot_id,
                "validation_passed": (
                    controller.validations[snapshot.snapshot_id].passed
                    if snapshot.snapshot_id in controller.validations
                    else None
                ),
                "solver_cells": len(controller.cells.get(snapshot.snapshot_id, ())),
                "instance_digest": (
                    controller.instances[snapshot.snapshot_id].instance_digest
                    if snapshot.snapshot_id in controller.instances
                    else None
                ),
                "reviewed": snapshot.design_digest in controller.promotions,
            }
            for snapshot in controller.snapshots
        ])
    return 0


def _epoch_record_review(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    answers = read_json(Path(args.answers))
    if not isinstance(answers, dict):
        raise ValueError("review answers must be one JSON object keyed by item ID")
    signing_key = Path(args.signing_key_file).read_bytes().strip()
    public_key = Path(args.public_key_file).read_bytes().strip()
    findings_value = read_json(Path(args.findings))
    if not isinstance(findings_value, dict):
        raise ValueError("review findings must be one JSON object")
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        snapshot = controller.snapshot_by_id(args.snapshot_id)
        record = controller.record_human_review(
            snapshot,
            args.reviewer_id,
            answers,
            PromotionDecision(args.decision),
            ReviewFindings(**findings_value),
            args.limitation,
            args.key_id,
            signing_key,
            public_key,
            args.prior_review_digest,
        )
        _print_json(record)
    return 0


def _epoch_freeze_audit(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        sandbox = SecureSandbox(
            memory_mb=controller.manifest.budget.memory_mb,
            process_limit=controller.manifest.budget.process_limit,
            cpu_seconds=controller.manifest.budget.cpu_seconds,
        )
        validator = PackageValidator(
            sandbox,
            sample_count=controller.manifest.thresholds.sample_count,
        )
        _print_json(build_public_audit_population(controller, validator))
    return 0


def _epoch_close(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        _print_json(controller.close_public_epoch())
    return 0


def _epoch_audit(args: argparse.Namespace) -> int:
    evidence = _evidence(args)
    with local_file_lock(evidence.root, f"epoch-{args.epoch_id}"):
        controller = _load_controller(evidence, args.epoch_id, _state(evidence))
        private = read_json(
            evidence.epoch_root(args.epoch_id) / "private" / "holdout-plan.json"
        )
        sandbox = SecureSandbox(
            memory_mb=controller.manifest.budget.memory_mb,
            process_limit=controller.manifest.budget.process_limit,
            cpu_seconds=controller.manifest.budget.cpu_seconds,
        )
        validator = PackageValidator(
            sandbox,
            sample_count=controller.manifest.thresholds.sample_count,
        )
        hidden = build_hidden_solver_backends(
            controller.manifest, private["hidden_solver_panel"]
        )
        _print_json(SealedAuditRunner(controller, validator, hidden).run())
    return 0


def _add_evidence_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--evidence-root",
        default=".bba",
        help="local BBA state and evidence directory (default: .bba)",
    )


def _add_epoch_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--epoch-id", required=True)
    _add_evidence_root(parser)


def _build_epoch_parser(commands: argparse._SubParsersAction) -> None:
    epoch = commands.add_parser("epoch", help="operate a restart-safe local epoch")
    epoch_commands = epoch.add_subparsers(dest="epoch_command", required=True)

    create = epoch_commands.add_parser(
        "create",
        help="create an epoch from BBA's built-in GCP serverless catalog",
    )
    create.add_argument(
        "--epoch-id",
        help="optional local epoch ID; BBA creates one when this option is absent",
    )
    _add_evidence_root(create)
    create.set_defaults(handler=_epoch_create)

    run = epoch_commands.add_parser("run", help="run or resume the public tournament")
    _add_epoch_id(run)
    run.set_defaults(handler=_epoch_run)

    preflight = epoch_commands.add_parser(
        "preflight", help="run the small paid Vertex model readiness check"
    )
    _add_epoch_id(preflight)
    preflight.set_defaults(handler=_epoch_preflight)

    status = epoch_commands.add_parser("status", help="show saved local progress")
    _add_epoch_id(status)
    status.set_defaults(handler=_epoch_status)

    candidates = epoch_commands.add_parser(
        "candidates", help="list saved candidate snapshots"
    )
    _add_epoch_id(candidates)
    candidates.set_defaults(handler=_epoch_candidates)

    review_items = epoch_commands.add_parser(
        "review-items", help="select the fixed six-item human review sample"
    )
    _add_epoch_id(review_items)
    review_items.add_argument("--snapshot-id", required=True)
    review_items.set_defaults(handler=_epoch_review_items)

    review = epoch_commands.add_parser(
        "record-review", help="sign and save a human promotion decision"
    )
    _add_epoch_id(review)
    review.add_argument("--snapshot-id", required=True)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument("--answers", required=True)
    review.add_argument("--findings", required=True)
    review.add_argument(
        "--decision",
        required=True,
        choices=[item.value for item in PromotionDecision],
    )
    review.add_argument("--limitation", action="append", default=[])
    review.add_argument("--key-id", required=True)
    review.add_argument("--signing-key-file", required=True)
    review.add_argument("--public-key-file", required=True)
    review.add_argument("--prior-review-digest")
    review.set_defaults(handler=_epoch_record_review)

    freeze = epoch_commands.add_parser(
        "freeze-audit", help="freeze public evaluator scores before holdout access"
    )
    _add_epoch_id(freeze)
    freeze.set_defaults(handler=_epoch_freeze_audit)

    close = epoch_commands.add_parser("close", help="publish the public evaluation")
    _add_epoch_id(close)
    close.set_defaults(handler=_epoch_close)

    audit = epoch_commands.add_parser("audit", help="open and score the sealed holdout")
    _add_epoch_id(audit)
    audit.set_defaults(handler=_epoch_audit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BenchBenchAgent local two-sided tournament controller"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify-package",
        help="validate an executable candidate in the secure sandbox",
    )
    verify.add_argument("--package", required=True)
    verify.add_argument("--seed", type=int, required=True)
    verify.add_argument("--sample-count", type=int, default=30)
    verify.add_argument("--timeout", type=int, default=600)
    verify.set_defaults(handler=_verify_package)
    status = commands.add_parser(
        "sandbox-status",
        help="report whether an audited OS sandbox is available",
    )
    status.set_defaults(handler=_sandbox_status)
    catalog = commands.add_parser(
        "catalog",
        help="show the BBA-owned GCP serverless model catalog",
    )
    catalog.set_defaults(handler=_catalog)
    evidence = commands.add_parser(
        "evidence", help="verify and replay immutable local evidence"
    )
    evidence_commands = evidence.add_subparsers(
        dest="evidence_command", required=True
    )
    replay = evidence_commands.add_parser(
        "replay-cell", help="replay one successful solver attempt"
    )
    _add_epoch_id(replay)
    replay.add_argument("--attempt-id", required=True)
    replay.set_defaults(handler=_evidence_replay_cell)
    _build_epoch_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"BBA error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry points for BBA protocol validation and conformance demos."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bba.audit import DefectPair
from bba.evidence import EvidenceStore
from bba.protocol import ExperimentManifest, ModelIdentity, PromotionDecision, digest_json, to_primitive
from bba.runtime import SecureSandbox
from bba.testing import CalibratedSolverFixture, ExecutableCreatorFixture, LocalFixtureSandbox
from bba.tournament import TournamentController
from bba.validator import PackageValidator, read_jsonl_strict


def _demo(args: argparse.Namespace) -> int:
    output = Path(args.out).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"demo output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    cohort = (
        ModelIdentity("fixture", "alpha", "family-a"),
        ModelIdentity("fixture", "beta", "family-b"),
        ModelIdentity("fixture", "gamma", "family-c"),
        ModelIdentity("fixture", "delta", "family-a"),
    )
    hidden = {
        "hidden_solver_panel": ["fixture-hidden-a", "fixture-hidden-b"],
        "hidden_seeds": [881, 883],
        "audit_policy": {"version": "fixture-audit-v1"},
    }
    manifest = ExperimentManifest(
        epoch_id=args.epoch,
        cohort=cohort,
        public_seed=20260811,
        hidden_commitments={key: digest_json(value) for key, value in hidden.items()},
        creator_prompt_digest=digest_json("fixture creator prompt"),
        solver_prompt_digest=digest_json("fixture solver prompt"),
        evaluator_version="fixture-public-evaluator-v1",
    )
    controller = TournamentController(
        manifest,
        EvidenceStore(output),
        PackageValidator(LocalFixtureSandbox(acknowledge_unsafe=True)),
        {
            identity.artifact_id: ExecutableCreatorFixture(bias)
            for identity, bias in zip(cohort, (0.20, 0.28, 0.36, 0.50))
        },
        {
            identity.artifact_id: CalibratedSolverFixture(skill)
            for identity, skill in zip(cohort, (0.45, 0.50, 0.55, 0.60))
        },
    )
    controller.run_public_epoch()
    for snapshot in [item for item in controller.snapshots if item.round_index == 2]:
        gold = {
            row["id"]: row["answer"]
            for row in read_jsonl_strict(Path(snapshot.package_path) / "gold_private_sample.jsonl")
        }
        selected = controller.select_review_items(snapshot)
        controller.record_human_review(
            snapshot,
            reviewer_id="fixture-independent-reviewer",
            reconstructed_answers={item_id: gold[item_id] for item_id in selected},
            decision=PromotionDecision.APPROVED,
            limitations=("Deterministic conformance fixture; not a scientific model result",),
            key_id="fixture-review-key",
            signing_key=b"fixture-only-signing-secret",
        )
    public_scores = {"good": 0.90, "okay": 0.70, "optimizer": 0.99, "damaged": 0.20}
    controller.freeze_audit_population(
        public_scores,
        [DefectPair("good", "damaged", "controlled_damage")],
    )
    public = controller.close_public_epoch()
    audit = controller.run_holdout_audit(
        {"good": 0.92, "okay": 0.68, "optimizer": 0.30, "damaged": 0.10},
        {"good": 0.95, "okay": 0.65, "optimizer": 0.05, "damaged": 0.15},
        hidden,
    )
    print(json.dumps({
        "epoch_id": manifest.epoch_id,
        "output": str(output),
        "snapshots": len(controller.snapshots),
        "solver_cells": sum(len(cells) for cells in controller.cells.values()),
        "final_creator_ranking": public["creator_rankings"]["final_round"],
        "solver_ranking": public["solver_ranking"],
        "audit_status": audit["status"],
    }, indent=2))
    return 0


def _verify_package(args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    validator = PackageValidator(sandbox, sample_count=args.sample_count, timeout_seconds=args.timeout)
    package = Path(args.package).resolve()
    from bba.evidence import tree_digest
    record = validator.validate(package, tree_digest(package), args.seed)
    print(json.dumps(to_primitive(record), indent=2, sort_keys=True))
    return 0 if record.passed else 1


def _sandbox_status(_args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    print(json.dumps({
        "backend": sandbox.backend,
        "available": sandbox.available,
        "unavailable_reason": sandbox.unavailable_reason or None,
        "fail_closed": True,
    }, indent=2))
    return 0 if sandbox.available else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BenchBenchAgent two-sided tournament controller")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the deterministic 4x4, three-round conformance fixture")
    demo.add_argument("--out", required=True, help="empty evidence directory")
    demo.add_argument("--epoch", default="conformance-demo")
    demo.set_defaults(handler=_demo)
    verify = commands.add_parser("verify-package", help="validate an executable candidate in the secure sandbox")
    verify.add_argument("--package", required=True)
    verify.add_argument("--seed", type=int, required=True)
    verify.add_argument("--sample-count", type=int, default=30)
    verify.add_argument("--timeout", type=int, default=600)
    verify.set_defaults(handler=_verify_package)
    status = commands.add_parser("sandbox-status", help="report whether an audited OS sandbox is available")
    status.set_defaults(handler=_sandbox_status)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"BBA error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

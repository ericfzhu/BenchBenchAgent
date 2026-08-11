"""Command-line entry points for BBA package validation and sandbox status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bba.evidence import tree_digest
from bba.protocol import to_primitive
from bba.runtime import SecureSandbox
from bba.validator import PackageValidator


def _verify_package(args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    validator = PackageValidator(sandbox, sample_count=args.sample_count, timeout_seconds=args.timeout)
    package = Path(args.package).resolve()
    record = validator.validate(package, tree_digest(package), args.seed)
    print(json.dumps(to_primitive(record), indent=2, sort_keys=True))
    return 0 if record.passed else 1


def _sandbox_status(_args: argparse.Namespace) -> int:
    sandbox = SecureSandbox()
    print(json.dumps(
        {
            "backend": sandbox.backend,
            "available": sandbox.available,
            "unavailable_reason": sandbox.unavailable_reason or None,
            "fail_closed": True,
        },
        indent=2,
    ))
    return 0 if sandbox.available else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BenchBenchAgent two-sided tournament controller"
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

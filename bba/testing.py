"""Deterministic test backends for protocol conformance tests.

These helpers never handle untrusted input.  Production code must use
``SecureSandbox`` and real provider backends.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from bba.protocol import ExperimentManifest, ModelIdentity
from bba.runtime import CommandResult


GENERATOR_SOURCE = r'''"""BBA_TEST_FIXTURE: deterministic arithmetic benchmark generator."""
import argparse
import json
import random
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out = Path(args.out_dir)
    spec = json.loads((out / "benchmark_spec.json").read_text())
    bias = float(spec["difficulty_bias"])
    rng = random.Random(args.seed)
    bundle = out / "solver_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    items, gold = [], []
    for index in range(args.sample_count):
        a, b, c = rng.randint(10, 90), rng.randint(10, 90), rng.randint(1, 200)
        difficulty = min(0.99, bias + (0.50 * index / max(1, args.sample_count - 1)))
        item_id = f"arith_{args.seed}_{index:04d}"
        items.append({
            "id": item_id,
            "a": a,
            "b": b,
            "c": c,
            "difficulty": round(difficulty, 6),
            "prompt": "Return (a * b + c) modulo 997 as an integer."
        })
        gold.append({"id": item_id, "answer": (a * b + c) % 997})
    (bundle / "items_private_sample.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in items), encoding="utf-8"
    )
    (out / "gold_private_sample.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in gold), encoding="utf-8"
    )
    (bundle / "SOLVER_MANIFEST.json").write_text(
        json.dumps({"schema_version": 1, "item_count": args.sample_count, "seed": args.seed}, sort_keys=True),
        encoding="utf-8",
    )
    (bundle / "solver_packet.md").write_text(
        "# Arithmetic audit\nFor each row return `(a * b + c) % 997` as an integer.\n", encoding="utf-8"
    )

if __name__ == "__main__":
    main()
'''


VERIFIER_SOURCE = r'''"""BBA_TEST_FIXTURE verifier."""
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--items", required=True)
parser.add_argument("--gold", required=True)
args = parser.parse_args()
items = [json.loads(line) for line in open(args.items, encoding="utf-8")]
gold = {row["id"]: row["answer"] for row in (json.loads(line) for line in open(args.gold, encoding="utf-8"))}
assert len(items) == len(gold)
for item in items:
    assert gold[item["id"]] == (item["a"] * item["b"] + item["c"]) % 997
'''


SCORER_SOURCE = r'''"""BBA_TEST_FIXTURE exact-match scorer."""
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--gold", required=True)
parser.add_argument("--predictions", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
gold = {row["id"]: row["answer"] for row in (json.loads(line) for line in open(args.gold, encoding="utf-8"))}
pred = {row["id"]: row["answer"] for row in (json.loads(line) for line in open(args.predictions, encoding="utf-8"))}
correct = sum(pred.get(item_id) == answer for item_id, answer in gold.items())
report = {"schema_version": 2, "total": len(gold), "correct": correct, "accuracy": correct / len(gold)}
open(args.out, "w", encoding="utf-8").write(json.dumps(report, sort_keys=True))
'''


class LocalFixtureSandbox:
    """Explicitly unsafe runner restricted to source-marked test fixtures."""

    backend = "trusted-fixture-only"
    available = True

    def __init__(self, acknowledge_unsafe: bool = False):
        if not acknowledge_unsafe:
            raise ValueError("LocalFixtureSandbox requires acknowledge_unsafe=True")

    def run_python(self, script, args, workspace, timeout_seconds, cwd=None):
        script = Path(script).resolve()
        workspace = Path(workspace).resolve()
        if workspace not in script.parents:
            raise ValueError("fixture script must live inside its temporary workspace")
        if "BBA_TEST_FIXTURE" not in script.read_text(encoding="utf-8")[:500]:
            raise ValueError("LocalFixtureSandbox refuses non-fixture generated code")
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(workspace / ".home"),
            "TMPDIR": str(workspace / ".tmp"),
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        Path(environment["HOME"]).mkdir(exist_ok=True)
        Path(environment["TMPDIR"]).mkdir(exist_ok=True)
        try:
            result = subprocess.run(
                [sys.executable, str(script)] + list(args),
                cwd=str(cwd or workspace),
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return CommandResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as exc:
            return CommandResult(-1, exc.stdout or "", exc.stderr or "", timed_out=True)


class ExecutableCreatorFixture:
    def __init__(self, base_bias: float):
        self.base_bias = base_bias

    def build(
        self,
        identity: ModelIdentity,
        round_index: int,
        output_dir: Path,
        feedback: Mapping[str, Any],
        parent_package: Optional[Path],
        manifest: ExperimentManifest,
    ) -> None:
        bias = min(0.90, self.base_bias + 0.08 * round_index)
        spec = {
            "schema_version": 1,
            "id": f"fixture-{identity.artifact_id}-r{round_index}",
            "capability_claim": "Following an explicit arithmetic rule under calibrated item difficulty",
            "creator": identity.artifact_id,
            "round": round_index,
            "difficulty_bias": bias,
            "feedback_source": feedback.get("source") if feedback else None,
        }
        files = {
            "README.md": "# Executable arithmetic fixture\nA deterministic BBA protocol fixture.\n",
            "benchmark_spec.json": json.dumps(spec, indent=2, sort_keys=True),
            "generator.py": GENERATOR_SOURCE,
            "verifier.py": VERIFIER_SOURCE,
            "scorer.py": SCORER_SOURCE,
            "validation_report.md": "# Validation\nExternally solvable from the public arithmetic rule.\n",
            "failure_modes.md": "# Failure modes\nArithmetic slips at higher calibrated difficulty.\n",
            "requirements.lock": "# Standard library only\n",
        }
        for name, content in files.items():
            (output_dir / name).write_text(content, encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(output_dir / "generator.py"),
                "--sample-count", str(manifest.thresholds.sample_count),
                "--seed", str(manifest.public_seed),
                "--out-dir", ".",
            ],
            cwd=str(output_dir),
            check=True,
            capture_output=True,
            text=True,
        )


class CalibratedSolverFixture:
    def __init__(self, skill: float):
        self.skill = skill

    def solve(self, identity, solver_bundle, items, repetition, manifest):
        threshold = self.skill + (-0.01, 0.0, 0.01)[repetition % 3]
        predictions = []
        for item in items:
            answer = (item["a"] * item["b"] + item["c"]) % 997
            if float(item["difficulty"]) > threshold:
                answer = (answer + 1) % 997
            predictions.append({"id": item["id"], "answer": answer})
        return predictions


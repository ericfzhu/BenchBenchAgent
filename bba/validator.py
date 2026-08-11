"""Controller-owned validation of executable benchmark packages."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from bba.evidence import tree_digest
from bba.protocol import ScoreSummary, ValidationRecord
from bba.runtime import SecureSandbox


REQUIRED_ROOT = (
    "README.md",
    "benchmark_spec.json",
    "generator.py",
    "verifier.py",
    "scorer.py",
    "gold_private_sample.jsonl",
    "validation_report.md",
    "failure_modes.md",
    "requirements.lock",
)
REQUIRED_PUBLIC = (
    "SOLVER_MANIFEST.json",
    "items_private_sample.jsonl",
)


def validate_artifact_tree(root: Path, max_files: int = 20000, max_bytes: int = 512 * 1024 * 1024) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("artifact root must be a real directory")
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symbolic link is prohibited: {path.relative_to(root)}")
        info = os.stat(path, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"special or hard-linked file is prohibited: {path.relative_to(root)}")
        files += 1
        size += info.st_size
        if files > max_files or size > max_bytes:
            raise ValueError("artifact exceeds controller resource limits")


def read_jsonl_strict(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line {number} in {path.name}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL line {number} in {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {number} is not an object")
        rows.append(value)
    return rows


def validate_answer_rows(rows: Sequence[Mapping[str, Any]], count: int, expected_ids: Optional[set] = None) -> set:
    if len(rows) != count:
        raise ValueError(f"expected {count} answers, found {len(rows)}")
    ids = []
    for index, row in enumerate(rows, 1):
        if set(row) != {"id", "answer"} or not isinstance(row["id"], str) or not row["id"]:
            raise ValueError(f"invalid answer row {index}")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate answer IDs")
    result = set(ids)
    if expected_ids is not None and result != expected_ids:
        raise ValueError("answer IDs do not match item IDs")
    return result


def validate_item_rows(rows: Sequence[Mapping[str, Any]], count: int) -> set:
    if len(rows) != count:
        raise ValueError(f"expected {count} items, found {len(rows)}")
    ids = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError(f"invalid item row {index}")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate item IDs")
    return set(ids)


def payload_digest(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / "gold_private_sample.jsonl"]
    bundle = root / "solver_bundle"
    if bundle.is_dir():
        paths.extend(sorted(path for path in bundle.rglob("*") if path.is_file()))
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def wrong_answer(answer: Any) -> Any:
    if isinstance(answer, bool):
        return not answer
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        return answer + 999999
    if isinstance(answer, str):
        return "__bba_wrong__" + answer
    if isinstance(answer, list):
        return answer + ["__bba_wrong__"]
    if isinstance(answer, dict):
        return dict(answer, __bba_wrong__=True)
    return "__bba_wrong__"


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def validate_lockfile(path: Path) -> None:
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or line.startswith(("-", "http:", "https:", "git+")):
            raise ValueError(f"requirements.lock line {number} is not an exact package pin")


def public_bundle_leaks(bundle: Path, gold: Sequence[Mapping[str, Any]]) -> List[str]:
    prohibited = ("gold", "answer_key", "solution", "generator.py", "verifier.py", "scorer.py")
    answer_map = {str(row["id"]): json.dumps(row["answer"], sort_keys=True) for row in gold}
    leaks = []
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix().lower()
        if any(term in relative for term in prohibited):
            leaks.append(f"prohibited-path:{relative}")
            continue
        if path.stat().st_size > 8_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for item_id, answer in answer_map.items():
            if item_id in text and answer in text:
                try:
                    parsed = json.loads(text) if path.suffix == ".json" else None
                except json.JSONDecodeError:
                    parsed = None
                if parsed is not None or f"{item_id}:{answer}" in text.replace(" ", ""):
                    leaks.append(f"answer-map:{path.relative_to(bundle)}:{item_id}")
    return sorted(set(leaks))


class PackageValidator:
    def __init__(self, sandbox: SecureSandbox, sample_count: int = 30, timeout_seconds: int = 600):
        self.sandbox = sandbox
        self.sample_count = sample_count
        self.timeout_seconds = timeout_seconds

    def _generate(self, package: Path, workspace: Path, seed: int) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        shutil.rmtree(package / "solver_bundle", ignore_errors=True)
        (package / "gold_private_sample.jsonl").unlink(missing_ok=True)
        result = self.sandbox.run_python(
            package / "generator.py",
            ["--sample-count", str(self.sample_count), "--seed", str(seed), "--out-dir", "."],
            workspace=workspace,
            cwd=package,
            timeout_seconds=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise ValueError(f"generator failed: {result.stderr[-1000:]}")
        validate_artifact_tree(package)
        bundle = package / "solver_bundle"
        for required in REQUIRED_PUBLIC:
            if not (bundle / required).is_file():
                raise ValueError(f"missing generated public artifact: solver_bundle/{required}")
        if not any((bundle / name).is_file() for name in ("README.md", "solver_packet.md")):
            raise ValueError("solver bundle has no public instructions")
        gold = read_jsonl_strict(package / "gold_private_sample.jsonl")
        items = read_jsonl_strict(bundle / "items_private_sample.jsonl")
        item_ids = validate_item_rows(items, self.sample_count)
        validate_answer_rows(gold, self.sample_count, expected_ids=item_ids)
        leaks = public_bundle_leaks(bundle, gold)
        if leaks:
            raise ValueError("public answer leakage: " + ", ".join(leaks[:10]))
        return payload_digest(package), gold, items

    def _score_controls(self, package: Path, workspace: Path, gold: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> None:
        verifier = self.sandbox.run_python(
            package / "verifier.py",
            ["--items", "solver_bundle/items_private_sample.jsonl", "--gold", "gold_private_sample.jsonl"],
            workspace=workspace,
            cwd=package,
            timeout_seconds=self.timeout_seconds,
        )
        if verifier.returncode != 0:
            raise ValueError(f"verifier failed: {verifier.stderr[-1000:]}")
        gold_predictions = package / ".controller_gold.jsonl"
        wrong_predictions = package / ".controller_wrong.jsonl"
        write_jsonl(gold_predictions, gold)
        write_jsonl(
            wrong_predictions,
            ({"id": row["id"], "answer": wrong_answer(row["answer"])} for row in gold),
        )
        for predictions, expected in ((gold_predictions, self.sample_count), (wrong_predictions, 0)):
            output = package / f".controller_score_{expected}.json"
            result = self.sandbox.run_python(
                package / "scorer.py",
                ["--gold", "gold_private_sample.jsonl", "--predictions", predictions.name, "--out", output.name],
                workspace=workspace,
                cwd=package,
                timeout_seconds=self.timeout_seconds,
            )
            if result.returncode != 0 or not output.is_file():
                raise ValueError(f"scorer failed for {predictions.name}: {result.stderr[-1000:]}")
            data = json.loads(output.read_text(encoding="utf-8"))
            summary = ScoreSummary(
                total=data.get("total"),
                correct=data.get("correct"),
                accuracy=data.get("accuracy"),
                schema_version=data.get("schema_version"),
            )
            if summary.correct != expected:
                raise ValueError(f"control score was {summary.correct}/{summary.total}, expected {expected}/{self.sample_count}")

    def validate(self, package_root: Path, candidate_digest: str, public_seed: int) -> ValidationRecord:
        checks: Dict[str, bool] = {}
        errors: List[str] = []
        same_digest: Optional[str] = None
        different_digest: Optional[str] = None
        try:
            package_root = Path(package_root).resolve()
            validate_artifact_tree(package_root)
            checks["artifact_tree"] = True
            missing = [name for name in REQUIRED_ROOT if not (package_root / name).is_file()]
            if missing:
                raise ValueError(f"missing required root files: {missing}")
            validate_lockfile(package_root / "requirements.lock")
            spec = json.loads((package_root / "benchmark_spec.json").read_text(encoding="utf-8"))
            if not str(spec.get("capability_claim", "")).strip():
                raise ValueError("benchmark_spec.json requires capability_claim")
            frozen_payload = payload_digest(package_root)
            checks["package_contract"] = True

            generated = []
            for seed in (public_seed, public_seed, public_seed + 1):
                with tempfile.TemporaryDirectory(prefix="bba-validate-") as temporary:
                    workspace = Path(temporary)
                    package = workspace / "candidate"
                    shutil.copytree(package_root, package)
                    digest, gold, items = self._generate(package, workspace, seed)
                    generated.append(digest)
                    if seed == public_seed:
                        self._score_controls(package, workspace, gold, items)
            if generated[0] != generated[1]:
                raise ValueError("same-seed generation is nondeterministic")
            if generated[0] == generated[2]:
                raise ValueError("designated different seeds produced identical payloads")
            if generated[0] != frozen_payload:
                raise ValueError("frozen package payload does not match clean regeneration")
            same_digest, different_digest = generated[0], generated[2]
            checks.update({
                "deterministic_generation": True,
                "seed_variation": True,
                "frozen_payload_match": True,
                "jsonl_contracts": True,
                "no_public_leakage": True,
                "gold_control": True,
                "wrong_control": True,
                "sandboxed_execution": True,
            })
        except Exception as exc:  # validation must return evidence, not erase the failure
            errors.append(str(exc))
        return ValidationRecord(
            candidate_digest=candidate_digest,
            passed=not errors,
            public_seed=public_seed,
            checks=checks,
            errors=tuple(errors),
            generated_payload_digest=same_digest,
            alternate_payload_digest=different_digest,
        )

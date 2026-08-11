"""Controlled package-damage operators for evaluator sensitivity audits."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict

from bba.validator import read_jsonl_strict, wrong_answer, write_jsonl


def _copy(source: Path, destination: Path) -> Path:
    if destination.exists():
        raise FileExistsError(f"damage variant already exists: {destination}")
    shutil.copytree(source, destination)
    return destination


def create_damage_variants(source: Path, output_root: Path) -> Dict[str, Path]:
    """Create immutable matched variants without modifying the base package."""

    source = Path(source).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    variants: Dict[str, Path] = {}

    corrupt = _copy(source, output_root / "corrupted_key")
    gold = read_jsonl_strict(corrupt / "gold_private_sample.jsonl")
    gold[0]["answer"] = wrong_answer(gold[0]["answer"])
    write_jsonl(corrupt / "gold_private_sample.jsonl", gold)
    variants["corrupted_key"] = corrupt

    duplicate = _copy(source, output_root / "duplicate_item")
    items_path = duplicate / "solver_bundle" / "items_private_sample.jsonl"
    items = read_jsonl_strict(items_path)
    items[-1] = dict(items[0])
    write_jsonl(items_path, items)
    variants["duplicate_item"] = duplicate

    truncated = _copy(source, output_root / "truncated")
    truncated_items = read_jsonl_strict(truncated / "solver_bundle" / "items_private_sample.jsonl")[:-1]
    truncated_gold = read_jsonl_strict(truncated / "gold_private_sample.jsonl")[:-1]
    write_jsonl(truncated / "solver_bundle" / "items_private_sample.jsonl", truncated_items)
    write_jsonl(truncated / "gold_private_sample.jsonl", truncated_gold)
    variants["truncated"] = truncated

    leaked = _copy(source, output_root / "answer_leak")
    shutil.copy2(
        leaked / "gold_private_sample.jsonl",
        leaked / "solver_bundle" / "answer_key.jsonl",
    )
    variants["answer_leak"] = leaked

    noop = _copy(source, output_root / "noop_generator")
    (noop / "generator.py").write_text(
        '"""BBA_TEST_FIXTURE controlled no-op generator."""\n',
        encoding="utf-8",
    )
    variants["noop_generator"] = noop
    return variants


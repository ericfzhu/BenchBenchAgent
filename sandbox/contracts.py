"""Strict schema validation, deterministic hashing, and anti-leakage audits."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def validate_artifact_tree(
    root_dir: str,
    max_files: int = 20000,
    max_bytes: int = 512 * 1024 * 1024,
) -> Tuple[bool, Optional[str]]:
    """Validates artifact directory tree, strictly rejecting symlinks, non-regular files,
    and enforcing file count and size limits.
    """
    root = Path(root_dir)
    if not root.exists() or not root.is_dir():
        return False, f"Directory does not exist: {root_dir}"

    total_files = 0
    total_bytes = 0

    IGNORED_DIRS = {"__pycache__", ".git", ".pytest_cache"}
    IGNORED_FILES = {".DS_Store"}

    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

        for d in dirnames:
            full_d = os.path.join(dirpath, d)
            if os.path.islink(full_d):
                return False, f"Symlink directory detected (security violation): {full_d}"

        for f in filenames:
            if f in IGNORED_FILES or f.endswith(".pyc"):
                continue
            full_f = os.path.join(dirpath, f)
            if os.path.islink(full_f):
                return False, f"Symlink file detected (security violation): {full_f}"
            if not os.path.isfile(full_f):
                return False, f"Non-regular file detected: {full_f}"

            total_files += 1
            if total_files > max_files:
                return False, f"File count exceeded limit ({total_files} > {max_files})"

            size = os.path.getsize(full_f)
            total_bytes += size
            if total_bytes > max_bytes:
                return False, f"Total byte size exceeded limit ({total_bytes} > {max_bytes})"

    return True, None


def tree_digest(root_dir: str) -> str:
    """Computes a deterministic SHA-256 digest of a directory tree.

    Uses 8-byte big-endian length prefixing for relative paths and file contents
    over lexicographically sorted relative paths, ignoring bytecode and OS cache files.
    """
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"Directory not found for digest: {root_dir}")

    IGNORED_DIRS = {"__pycache__", ".git", ".pytest_cache"}
    IGNORED_FILES = {".DS_Store"}

    rel_paths: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for f in filenames:
            if f in IGNORED_FILES or f.endswith(".pyc"):
                continue
            full_f = os.path.join(dirpath, f)
            rel = os.path.relpath(full_f, root_dir).replace("\\", "/")
            rel_paths.append(rel)

    rel_paths.sort()

    hasher = hashlib.sha256()
    for rel in rel_paths:
        full_f = os.path.join(root_dir, rel)
        rel_bytes = rel.encode("utf-8")
        hasher.update(len(rel_bytes).to_bytes(8, byteorder="big"))
        hasher.update(rel_bytes)

        with open(full_f, "rb") as f:
            content = f.read()
        hasher.update(len(content).to_bytes(8, byteorder="big"))
        hasher.update(content)

    return hasher.hexdigest()


def generated_payload_digest(output_dir: str) -> str:
    """Computes deterministic SHA-256 digest of generated benchmark payload."""
    return tree_digest(output_dir)


def read_jsonl_strict(file_path: str) -> List[Dict[str, Any]]:
    """Strictly parses JSONL file line by line."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {file_path}")

    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                if not isinstance(data, dict):
                    raise ValueError(f"Line {idx} in {file_path} is not a JSON object: {line_str}")
                rows.append(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON decode error at line {idx} in {file_path}: {e}") from e

    return rows


def validate_answer_rows(
    rows: List[Dict[str, Any]],
    expected_count: int = 30,
) -> Tuple[bool, Optional[str]]:
    """Validates ground truth or prediction answer rows.

    Each row must have non-empty 'id' and 'answer' representing integer cents.
    """
    if len(rows) != expected_count:
        return False, f"Expected {expected_count} answer rows, got {len(rows)}"

    seen_ids = set()
    for idx, row in enumerate(rows, 1):
        if "id" not in row or not isinstance(row["id"], str) or not row["id"].strip():
            return False, f"Row {idx} missing valid 'id' string: {row}"
        row_id = row["id"].strip()
        if row_id in seen_ids:
            return False, f"Duplicate item id '{row_id}' found in row {idx}"
        seen_ids.add(row_id)

        if "answer" not in row:
            return False, f"Row {idx} (id={row_id}) missing 'answer' field"
        ans = str(row["answer"]).strip()
        if not ans:
            return False, f"Row {idx} (id={row_id}) has empty answer"
        try:
            int(ans)
        except ValueError:
            return False, f"Row {idx} (id={row_id}) answer '{ans}' is not a valid integer USD cent value"

    return True, None


def validate_item_rows(
    rows: List[Dict[str, Any]],
    expected_count: int = 30,
) -> Tuple[bool, Optional[str]]:
    """Validates problem items in items_private_sample.jsonl."""
    if len(rows) != expected_count:
        return False, f"Expected {expected_count} item rows, got {len(rows)}"

    seen_ids = set()
    for idx, row in enumerate(rows, 1):
        if "id" not in row or not isinstance(row["id"], str) or not row["id"].strip():
            return False, f"Row {idx} missing valid 'id' string: {row}"
        row_id = row["id"].strip()
        if row_id in seen_ids:
            return False, f"Duplicate item id '{row_id}' in row {idx}"
        seen_ids.add(row_id)

        prompt = row.get("prompt") or row.get("task") or row.get("question")
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            return False, f"Row {idx} (id={row_id}) missing prompt/task text"

    return True, None


def bundle_leaks(solver_bundle_dir: str, gold_rows: List[Dict[str, Any]]) -> List[str]:
    """Scans solver_bundle/ to verify zero leakage of gold keys or ground truth answers."""
    import re
    bundle_path = Path(solver_bundle_dir)
    if not bundle_path.exists() or not bundle_path.is_dir():
        return [f"Solver bundle directory not found: {solver_bundle_dir}"]

    leaks: List[str] = []
    forbidden_terms = [
        "gold_private_sample",
        "ground_truth",
        "verifier.py",
        "negative_control_sample",
    ]

    for dirpath, _, filenames in os.walk(solver_bundle_dir):
        for f in filenames:
            full_path = os.path.join(dirpath, f)
            rel_path = os.path.relpath(full_path, solver_bundle_dir)

            for term in forbidden_terms:
                if term in rel_path:
                    leaks.append(f"Forbidden file or path in solver bundle: {rel_path}")

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                    content = file_obj.read()
            except Exception:
                continue

            for term in forbidden_terms:
                if term in content:
                    leaks.append(f"Leak of forbidden token '{term}' in {rel_path}")

            for row in gold_rows:
                item_id = str(row.get("id", "")).strip()
                ans_str = str(row.get("answer", "")).strip()
                if not item_id or not ans_str:
                    continue
                # Look for direct mappings in JSON, text, or assignment forms
                escaped_id = re.escape(item_id)
                escaped_ans = re.escape(ans_str)
                patterns = [
                    rf'"{escaped_id}"\s*:\s*"{escaped_ans}"',
                    rf'"{escaped_id}"\s*:\s*{escaped_ans}\b',
                    rf'id\s*=\s*[\'"]{escaped_id}[\'"].*?answer\s*=\s*[\'"]?{escaped_ans}[\'"]?',
                ]
                for pat in patterns:
                    if re.search(pat, content, re.DOTALL):
                        leaks.append(f"Direct gold mapping leak for {item_id} in {rel_path}")
                        break

    return leaks


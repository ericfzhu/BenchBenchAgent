"""Solver tools: Isolated Python REPL with Decimal arithmetic, safe asset reader, and prediction submitter."""

import csv
import io
import json
import math
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def python_repl_tool(code: str) -> str:
    """Executes Python code in a safe evaluation environment with Decimal math pre-imported.

    Returns the standard output or evaluated result.
    """
    stdout_capture = io.StringIO()
    safe_globals: Dict[str, Any] = {
        "__builtins__": __builtins__,
        "Decimal": Decimal,
        "ROUND_HALF_UP": ROUND_HALF_UP,
        "json": json,
        "csv": csv,
        "math": math,
        "Path": Path,
    }
    local_scope: Dict[str, Any] = {}

    old_stdout = sys.stdout
    try:
        sys.stdout = stdout_capture
        # Try evaluating as single expression first
        try:
            compiled = compile(code.strip(), "<repl>", "eval")
            eval_res = eval(compiled, safe_globals, local_scope)
            if eval_res is not None:
                print(eval_res)
        except SyntaxError:
            # Fall back to exec for multi-line statements
            compiled = compile(code, "<repl>", "exec")
            exec(compiled, safe_globals, local_scope)

        out = stdout_capture.getvalue()
        if not out and local_scope:
            # If nothing printed, display assigned variables
            out = str({k: v for k, v in local_scope.items() if not k.startswith("_")})
        return out.strip()
    except Exception as e:
        return f"REPL Execution Error: {type(e).__name__}: {e}"
    finally:
        sys.stdout = old_stdout


def read_solver_asset(bundle_dir: str, rel_path: str) -> str:
    """Safely reads an asset file within solver_bundle, strictly preventing path traversal."""
    base = Path(bundle_dir).resolve()
    target = (base / rel_path).resolve()

    # Path traversal check
    if not target.is_relative_to(base):
        raise ValueError(f"Security violation: path traversal detected: {rel_path}")

    if not target.exists():
        raise FileNotFoundError(f"Asset not found: {rel_path} in {bundle_dir}")

    if not target.is_file():
        raise ValueError(f"Asset path is not a regular file: {rel_path}")

    with open(target, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def list_solver_assets(bundle_dir: str, sub_path: str = "") -> List[str]:
    """Safely lists files in a sub-path within solver_bundle."""
    base = Path(bundle_dir).resolve()
    target = (base / sub_path).resolve() if sub_path else base

    if not target.is_relative_to(base):
        raise ValueError(f"Security violation: path traversal detected: {sub_path}")

    if not target.exists() or not target.is_dir():
        return []

    entries: List[str] = []
    for root, _, files in os.walk(target):
        for f in files:
            full = Path(root) / f
            entries.append(str(full.relative_to(base)).replace("\\", "/"))
    entries.sort()
    return entries


def submit_prediction_row(
    item_id: str,
    answer: Union[str, int, float, Decimal],
    predictions_path: str,
) -> Dict[str, Any]:
    """Validates and appends a single prediction row to predictions.jsonl."""
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError(f"Invalid item_id: {item_id}")

    try:
        if isinstance(answer, Decimal):
            ans_int = int(answer.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        else:
            ans_dec = Decimal(str(answer).strip())
            ans_int = int(ans_dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception as e:
        raise ValueError(f"Prediction answer '{answer}' cannot be converted to integer cents: {e}") from e

    row = {"id": item_id.strip(), "answer": str(ans_int)}

    pred_file = Path(predictions_path)
    pred_file.parent.mkdir(parents=True, exist_ok=True)

    with open(pred_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    return {"status": "SUCCESS", "id": row["id"], "answer": row["answer"]}


"""Public entrypoint for the spatial command deck and local operator console."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from bba._web import app, create_app, get_app, run_console
from bba.operator import OperatorConsole

__all__ = ["app", "create_app", "get_app", "run_console", "uvicorn"]

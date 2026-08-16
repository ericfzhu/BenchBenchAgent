"""Public entrypoint for the phase-aware local development portal."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from bba._portal import *  # noqa: F401,F403
from bba._portal import create_app as _create_portal_app
from bba.operator import OperatorConsole
from bba.portal_candidate import install_candidate_page


def create_app(console: OperatorConsole):
    return install_candidate_page(_create_portal_app(console), console)


def run_console(evidence_root: Path, port: int = 8765) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    console = OperatorConsole(evidence_root)
    uvicorn.run(
        create_app(console),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )

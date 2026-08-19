"""Entrypoint facade for the web console implementation."""

from __future__ import annotations

from bba._web import (
    app,
    create_app,
    get_app,
    run_console,
)

__all__ = ["app", "create_app", "get_app", "run_console"]

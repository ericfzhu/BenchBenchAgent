"""Compatibility facade for candidate page installation."""

from __future__ import annotations


def install_candidate_page(app, console):
    """Compatibility pass-through since routes are natively registered in create_app."""
    return app

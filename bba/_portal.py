"""Compatibility facade for the local development portal implementation."""

from bba.portal_app import *  # noqa: F401,F403
from bba.portal_app import (
    CSS,
    _chip,
    _csrf,
    _drop_get_route,
    _e,
    _epoch_link,
    _layout,
    _phase_percent,
    _tone,
    _u,
    create_app,
)

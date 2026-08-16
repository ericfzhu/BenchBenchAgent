"""Public sealed-audit API with frozen sandbox conformance checks."""

from __future__ import annotations

from bba._audit_runner import *  # noqa: F401,F403
from bba._audit_runner import (
    SealedAuditRunner as _SealedAuditRunner,
    build_public_audit_population as _build_public_audit_population,
)


def _validate_audit_sandbox(controller, validator) -> None:
    sandbox = validator.sandbox
    if not sandbox.available:
        raise RuntimeError(
            sandbox.unavailable_reason or "secure local sandbox is unavailable"
        )
    if sandbox.backend != controller.manifest.sandbox.backend:
        raise ValueError("audit sandbox does not match the frozen epoch manifest")


def build_public_audit_population(controller, validator):
    _validate_audit_sandbox(controller, validator)
    return _build_public_audit_population(controller, validator)


class SealedAuditRunner(_SealedAuditRunner):
    def __init__(self, controller, validator, hidden_solver_backends):
        _validate_audit_sandbox(controller, validator)
        super().__init__(controller, validator, hidden_solver_backends)

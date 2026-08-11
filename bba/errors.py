"""Execution failures shared by provider backends and the tournament."""


class ProviderFailure(RuntimeError):
    """The configured model provider could not complete an invocation."""


class SolverTimedOut(RuntimeError):
    """A solver invocation exceeded its frozen wall-clock budget."""


class PredictionParseFailure(RuntimeError):
    """A solver returned output that cannot satisfy the prediction contract."""

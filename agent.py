"""Google ADK discovery entrypoint for BenchBenchAgent.

The ADK CLI discovers ``root_agent`` from this module. Tournament creator and
solver execution is implemented by :mod:`bba.adk_runtime`.
"""

import os

from bba.adk_runtime import build_protocol_agent


root_agent = build_protocol_agent(
    os.environ.get("BBA_ADK_MODEL", "gemini-2.5-flash")
)

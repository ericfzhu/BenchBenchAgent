"""Google ADK 2.6.3 creator and solver execution backends.

ADK owns model turns, tool calls, sessions, and event streams.  The trusted
``TournamentController`` remains responsible for package validation, scoring,
promotion, rankings, and the sealed evaluator audit.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union
from uuid import uuid4

import google.adk
from google.adk import Agent, Runner
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.models import BaseLlm
from google.adk.models.registry import LLMRegistry
from google.adk.plugins import BasePlugin
from google.adk.sessions import InMemorySessionService
from google.adk.telemetry import ContentCapturingMode, TelemetryConfig
from google.genai import types

from bba.errors import PredictionParseFailure, ProviderFailure, SolverTimedOut
from bba.gcp import configure_gcp_environment
from bba.observability import LocalObservabilityStore
from bba.protocol import (
    ExperimentManifest,
    ModelIdentity,
    SolverDebrief,
    digest_json,
    solver_debrief_from_mapping,
    to_primitive,
)
from bba.runtime import SecureSandbox


ADK_VERSION = google.adk.__version__
SUPPORTED_ADK_VERSION = "2.6.3"

if ADK_VERSION != SUPPORTED_ADK_VERSION:
    raise RuntimeError(
        f"BBA requires google-adk {SUPPORTED_ADK_VERSION}; found {ADK_VERSION}"
    )


CREATOR_INSTRUCTION = """You are a benchmark creator in a BenchBenchAgent epoch.

Build one executable benchmark design in the provided empty or parent-derived
workspace. The design must state a meaningful capability, generate deterministic
private instances from controller-selected seeds, and include an independent
verifier and exact-match scorer. The controller selects the evaluation seed only
after every design in the round is frozen. Use only the candidate
filesystem tools. Generated code may be executed only with
run_candidate_python, which uses the controller's credential-free sandbox.

Required package files:
- README.md
- benchmark_spec.json
- generator.py
- verifier.py
- scorer.py
- validation_report.md
- failure_modes.md
- requirements.lock
- solver_bundle/README.md or solver_bundle/solver_packet.md

Do not generate evaluation items or gold during construction. Never place gold
answers, answer mappings, private diagnostics, or hidden audit material in
solver_bundle. Call finish_candidate only after the design is
complete and you have checked it as far as the available tools permit.
"""


SOLVER_INSTRUCTION = """You are a blind solver in a BenchBenchAgent epoch.

You may inspect only the isolated solver bundle exposed by the bundle tools.
Solve every declared item under the frozen budget. Submit exactly one JSON
answer for every item with submit_predictions. After BBA locks the predictions,
submit one concise diagnostic for every item with submit_debrief. The debrief
must describe the approach, public evidence, uncertainty, and confidence. It
cannot change a locked answer. Do not assume access to creator
files, private gold, other candidates, prior repetitions, or hidden audit
evidence. A final prose answer does not count as a submission.
"""


ModelLike = Union[str, BaseLlm]


@dataclass(frozen=True)
class AdkInvocationTrace:
    """Redacted, controller-publishable evidence from one isolated ADK run."""

    schema_version: int
    adk_version: str
    role: str
    identity: ModelIdentity
    session_id: str
    invocation_id: str
    model_calls: int
    tool_calls: Tuple[str, ...]
    event_digests: Tuple[str, ...]
    final_response_digest: Optional[str]
    prompt_tokens: int
    output_tokens: int
    total_tokens: int
    response_model_versions: Tuple[str, ...]
    usage_metadata_complete: bool
    started_at: str
    finished_at: str
    status: str
    behavior_settings: Mapping[str, Any]
    duration_ms: float
    model_duration_ms: float
    tool_duration_ms: float
    model_errors: int
    tool_errors: int
    error: Optional[str] = None


class _ObservabilityPlugin(BasePlugin):
    """Apply budgets and capture redacted ADK lifecycle telemetry."""

    def __init__(
        self,
        token_budget: int,
        behavior_settings: Mapping[str, Any],
        *,
        epoch_id: str,
        role: str,
        identity: ModelIdentity,
        session_id: str,
        invocation_id: str,
        store: Optional[LocalObservabilityStore],
    ) -> None:
        super().__init__(name=f"bba-observability-{uuid4().hex}")
        self.token_budget = token_budget
        self.behavior_settings = dict(behavior_settings)
        self.epoch_id = epoch_id
        self.role = role
        self.identity = identity
        self.session_id = session_id
        self.invocation_id = invocation_id
        self.store = store
        self.observation_id = uuid4().hex
        self.started_at = _utc_now()
        self.started_ns = time.monotonic_ns()
        self.model_calls = 0
        self.usage_reports = 0
        self.tool_calls: list[str] = []
        self.event_digests: list[str] = []
        self.final_response_digest = None
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.response_model_versions: list[str] = []
        self.model_duration_ms = 0.0
        self.tool_duration_ms = 0.0
        self.model_errors = 0
        self.tool_errors = 0
        self._model_started_ns: list[int] = []
        self._tool_started_ns: dict[str, list[int]] = {}

    def _record(self, status: str, error_type: Optional[str] = None) -> None:
        if self.store is None:
            return
        self.store.update({
            "schema_version": 1,
            "observation_id": self.observation_id,
            "epoch_id": self.epoch_id,
            "role": self.role,
            "identity": to_primitive(self.identity),
            "session_id": self.session_id,
            "invocation_id": self.invocation_id,
            "adk_version": ADK_VERSION,
            "status": status,
            "started_at": self.started_at,
            "duration_ms": round((time.monotonic_ns() - self.started_ns) / 1_000_000, 3),
            "model_calls": self.model_calls,
            "tool_call_count": len(self.tool_calls),
            "tool_calls": tuple(self.tool_calls),
            "event_count": len(self.event_digests),
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "usage_metadata_complete": self.usage_reports == self.model_calls,
            "model_duration_ms": round(self.model_duration_ms, 3),
            "tool_duration_ms": round(self.tool_duration_ms, 3),
            "model_errors": self.model_errors,
            "tool_errors": self.tool_errors,
            "error_type": error_type,
            "content_captured": False,
        })

    def finish(self, status: str, error: Optional[BaseException]) -> None:
        finished_ns = time.monotonic_ns()
        while self._model_started_ns:
            self.model_duration_ms += (
                finished_ns - self._model_started_ns.pop()
            ) / 1_000_000
        for started_values in self._tool_started_ns.values():
            while started_values:
                self.tool_duration_ms += (
                    finished_ns - started_values.pop()
                ) / 1_000_000
        self._record(status, type(error).__name__ if error is not None else None)

    async def before_run_callback(self, *, invocation_context):
        self._record("running")
        return None

    async def before_model_callback(self, *, callback_context, llm_request):
        remaining = self.token_budget - self.total_tokens
        if remaining <= 0:
            raise RuntimeError("frozen ADK token budget exhausted")
        if llm_request.config is None:
            llm_request.config = types.GenerateContentConfig(max_output_tokens=remaining)
        else:
            configured = llm_request.config.max_output_tokens
            llm_request.config.max_output_tokens = min(configured or remaining, remaining)
        for name in ("temperature", "top_p"):
            value = self.behavior_settings.get(name)
            if value is not None:
                setattr(llm_request.config, name, value)
        self.model_calls += 1
        self._model_started_ns.append(time.monotonic_ns())
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        if self._model_started_ns:
            self.model_duration_ms += (
                time.monotonic_ns() - self._model_started_ns.pop()
            ) / 1_000_000
        if llm_response.model_version:
            self.response_model_versions.append(str(llm_response.model_version))
        usage = llm_response.usage_metadata
        if usage is not None:
            self.usage_reports += 1
            self.prompt_tokens += int(getattr(usage, "prompt_token_count", 0) or 0)
            self.output_tokens += int(getattr(usage, "candidates_token_count", 0) or 0)
            self.total_tokens += int(getattr(usage, "total_token_count", 0) or 0)
            if self.total_tokens > self.token_budget:
                raise RuntimeError("frozen ADK token budget exceeded")
        return None

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        if self._model_started_ns:
            self.model_duration_ms += (
                time.monotonic_ns() - self._model_started_ns.pop()
            ) / 1_000_000
        self.model_errors += 1
        return None

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        self.tool_calls.append(tool.name)
        self._tool_started_ns.setdefault(tool.name, []).append(time.monotonic_ns())
        return None

    def _finish_tool(self, name: str) -> None:
        started = self._tool_started_ns.get(name, [])
        if started:
            self.tool_duration_ms += (time.monotonic_ns() - started.pop()) / 1_000_000

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        self._finish_tool(tool.name)
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        self._finish_tool(tool.name)
        self.tool_errors += 1
        return None

    async def on_event_callback(self, *, invocation_context, event):
        primitive = event.model_dump(mode="json", by_alias=False, exclude_none=True)
        event_digest = digest_json(primitive)
        self.event_digests.append(event_digest)
        if event.is_final_response():
            self.final_response_digest = event_digest
        return None

    async def on_run_error_callback(self, *, invocation_context, error):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative_path(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise ValueError("path must be a non-empty POSIX relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("path must remain inside the isolated workspace")
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("symbolic links are forbidden")
    target = root.joinpath(*pure.parts)
    if target.is_symlink():
        raise ValueError("symbolic links are forbidden")
    return target


def _tree_entries(root: Path) -> Sequence[Path]:
    entries = list(root.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("symbolic links are forbidden")
    return entries


def _copy_parent(parent: Path, output: Path) -> None:
    if not parent.is_dir():
        raise ValueError("parent candidate snapshot is unavailable")
    _tree_entries(parent)
    shutil.copytree(parent, output, dirs_exist_ok=True, symlinks=False)


def _session_token(payload: Mapping[str, Any]) -> str:
    return digest_json(payload)[:24]


def _private_telemetry_config() -> TelemetryConfig:
    """Return ADK telemetry settings that cannot record message content."""

    config = TelemetryConfig(
        genai_semconv_stability_opt_in="stable",
        capture_message_content=ContentCapturingMode.NO_CONTENT,
    )
    if (
        config.should_add_content_to_logs
        or config.should_add_content_to_experimental_spans
        or config.should_add_content_to_legacy_spans
    ):
        raise RuntimeError("ADK message-content telemetry must remain disabled")
    return config


async def _run_agent(
    *,
    agent: Agent,
    role: str,
    identity: ModelIdentity,
    message: str,
    timeout_seconds: int,
    max_llm_calls: int,
    token_budget: int,
    session_token: str,
    trace_callback: Callable[[AdkInvocationTrace], None],
    epoch_id: str,
    observability_store: Optional[LocalObservabilityStore],
) -> None:
    app_name = f"bba_{role}"
    session_id = f"{role}-{session_token}"
    invocation_id = f"inv-{session_token}"
    user_id = identity.artifact_id
    plugin = _ObservabilityPlugin(
        token_budget,
        identity.behavior_settings,
        epoch_id=epoch_id,
        role=role,
        identity=identity,
        session_id=session_id,
        invocation_id=invocation_id,
        store=observability_store,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    runner = Runner(
        app=App(name=app_name, root_agent=agent, plugins=[plugin]),
        session_service=session_service,
    )
    started_at = _utc_now()
    status = "success"
    error = None

    async def consume_events() -> None:
        new_message = types.Content(role="user", parts=[types.Part(text=message)])
        async for _event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=new_message,
            run_config=RunConfig(
                max_llm_calls=max_llm_calls,
                custom_metadata={
                    "bba.epoch_id": epoch_id,
                    "bba.role": role,
                    "bba.identity": identity.artifact_id,
                },
                telemetry=_private_telemetry_config(),
            ),
        ):
            pass

    caught: Optional[BaseException] = None
    try:
        await asyncio.wait_for(consume_events(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        caught = exc
        status = "timeout"
        error = f"ADK {role} invocation exceeded {timeout_seconds} seconds"
        raise exc
    except Exception as exc:
        caught = exc
        status = "provider_error"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            await runner.close()
        finally:
            plugin.finish(status, caught)
            finished_at = _utc_now()
            trace_callback(AdkInvocationTrace(
                schema_version=2,
                adk_version=ADK_VERSION,
                role=role,
                identity=identity,
                session_id=session_id,
                invocation_id=invocation_id,
                model_calls=plugin.model_calls,
                tool_calls=tuple(plugin.tool_calls),
                event_digests=tuple(plugin.event_digests),
                final_response_digest=plugin.final_response_digest,
                prompt_tokens=plugin.prompt_tokens,
                output_tokens=plugin.output_tokens,
                total_tokens=plugin.total_tokens,
                response_model_versions=tuple(plugin.response_model_versions),
                usage_metadata_complete=plugin.usage_reports == plugin.model_calls,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                behavior_settings=dict(identity.behavior_settings),
                duration_ms=round((time.monotonic_ns() - plugin.started_ns) / 1_000_000, 3),
                model_duration_ms=round(plugin.model_duration_ms, 3),
                tool_duration_ms=round(plugin.tool_duration_ms, 3),
                model_errors=plugin.model_errors,
                tool_errors=plugin.tool_errors,
                error=error,
            ))


class _TraceBackend:
    def __init__(
        self,
        require_usage_metadata: bool,
        observability_store: Optional[LocalObservabilityStore],
    ) -> None:
        self._last_trace: Optional[AdkInvocationTrace] = None
        self._require_usage_metadata = require_usage_metadata
        self._observability_store = observability_store

    def _save_trace(self, trace: AdkInvocationTrace) -> None:
        self._last_trace = trace

    def take_trace(self) -> Optional[AdkInvocationTrace]:
        trace = self._last_trace
        self._last_trace = None
        return trace

    def _verify_usage_metadata(self) -> None:
        if (
            self._require_usage_metadata
            and self._last_trace is not None
            and not self._last_trace.usage_metadata_complete
        ):
            raise ProviderFailure(
                "ADK provider omitted token usage required for budget enforcement"
            )


class AdkCreatorBackend(_TraceBackend):
    """A real ADK agent with workspace-scoped construction tools."""

    def __init__(
        self,
        model: ModelLike,
        *,
        instruction: str = CREATOR_INSTRUCTION,
        construction_sandbox: Optional[SecureSandbox] = None,
        max_files: int = 512,
        max_bytes: int = 16 * 1024 * 1024,
        require_usage_metadata: bool = True,
        observability_store: Optional[LocalObservabilityStore] = None,
    ) -> None:
        super().__init__(require_usage_metadata, observability_store)
        self.model = model
        self.instruction = instruction
        self.prompt_digest = digest_json(instruction)
        self.construction_sandbox = construction_sandbox or SecureSandbox()
        self.max_files = max_files
        self.max_bytes = max_bytes

    def build(
        self,
        identity: ModelIdentity,
        round_index: int,
        output_dir: Path,
        feedback: Mapping[str, Any],
        parent_package: Optional[Path],
        manifest: ExperimentManifest,
    ) -> None:
        output_dir = Path(output_dir).resolve()
        if parent_package is not None:
            _copy_parent(Path(parent_package).resolve(), output_dir)
        lock = threading.Lock()
        finished = False

        def list_candidate_files() -> Dict[str, Any]:
            """List files currently present in the candidate workspace."""
            files = [
                {"path": str(path.relative_to(output_dir)), "bytes": path.stat().st_size}
                for path in _tree_entries(output_dir)
                if path.is_file()
            ]
            return {"files": sorted(files, key=lambda row: row["path"])}

        def read_candidate_file(path: str, offset: int = 0, limit: int = 65536) -> Dict[str, Any]:
            """Read a UTF-8 candidate file chunk.

            Args:
                path: POSIX path relative to the candidate workspace.
                offset: Byte offset at which reading starts.
                limit: Maximum number of bytes to return.
            """
            target = _safe_relative_path(output_dir, path)
            if not target.is_file() or offset < 0 or not 1 <= limit <= 262144:
                raise ValueError("invalid candidate read request")
            data = target.read_bytes()[offset:offset + limit]
            total_bytes = target.stat().st_size
            return {
                "path": path,
                "offset": offset,
                "total_bytes": total_bytes,
                "eof": offset + len(data) >= total_bytes,
                "content": data.decode("utf-8"),
            }

        def write_candidate_file(path: str, content: str) -> Dict[str, Any]:
            """Atomically create or replace one UTF-8 candidate file.

            Args:
                path: POSIX path relative to the candidate workspace.
                content: Complete UTF-8 file content.
            """
            nonlocal finished
            data = content.encode("utf-8")
            if len(data) > self.max_bytes:
                raise ValueError("single file exceeds the candidate byte limit")
            with lock:
                target = _safe_relative_path(output_dir, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                old_size = target.stat().st_size if target.is_file() else 0
                files = [entry for entry in _tree_entries(output_dir) if entry.is_file()]
                total = sum(entry.stat().st_size for entry in files) - old_size + len(data)
                prospective_count = len(files) + (0 if target.is_file() else 1)
                if prospective_count > self.max_files or total > self.max_bytes:
                    raise ValueError("candidate workspace limit exceeded")
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                temporary.write_bytes(data)
                temporary.replace(target)
                finished = False
            return {
                "path": path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        def delete_candidate_file(path: str) -> Dict[str, Any]:
            """Delete one regular candidate file during an adaptive repair.

            Args:
                path: POSIX path relative to the candidate workspace.
            """
            nonlocal finished
            with lock:
                target = _safe_relative_path(output_dir, path)
                if not target.is_file():
                    raise ValueError("only regular candidate files may be deleted")
                target.unlink()
                finished = False
            return {"path": path, "deleted": True}

        def run_candidate_python(script: str, args: list[str]) -> Dict[str, Any]:
            """Run candidate Python in the credential-free construction sandbox.

            Args:
                script: Python file relative to the candidate workspace.
                args: Command-line arguments passed to the script.
            """
            target = _safe_relative_path(output_dir, script)
            if not target.is_file() or target.suffix != ".py":
                raise ValueError("script must name a candidate Python file")
            result = self.construction_sandbox.run_python(
                target,
                [str(arg) for arg in args],
                workspace=output_dir,
                cwd=output_dir,
                timeout_seconds=min(120, manifest.budget.creator_seconds),
            )
            return {
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "stdout": result.stdout[-8000:],
                "stderr": result.stderr[-8000:],
            }

        def finish_candidate() -> Dict[str, Any]:
            """Declare that the executable benchmark design is complete."""
            nonlocal finished
            finished = True
            return {"complete": True, "file_count": len(list_candidate_files()["files"])}

        agent = Agent(
            name=("creator_" + re.sub(r"\W", "_", identity.artifact_id))[:120],
            model=self.model,
            instruction=self.instruction,
            tools=[
                list_candidate_files,
                read_candidate_file,
                write_candidate_file,
                delete_candidate_file,
                run_candidate_python,
                finish_candidate,
            ],
            mode="chat",
        )
        message = json.dumps({
            "epoch_digest": manifest.digest,
            "role": "creator",
            "creator": to_primitive(identity),
            "round": round_index,
            "sample_count": manifest.thresholds.sample_count,
            "budget": to_primitive(manifest.budget),
            "has_parent_snapshot": parent_package is not None,
            "public_feedback": to_primitive(feedback),
        }, sort_keys=True)
        token = _session_token({
            "epoch": manifest.digest,
            "role": "creator",
            "identity": identity.artifact_id,
            "round": round_index,
        })
        try:
            asyncio.run(_run_agent(
                agent=agent,
                role="creator",
                identity=identity,
                message=message,
                timeout_seconds=manifest.budget.creator_seconds,
                max_llm_calls=manifest.budget.max_llm_calls,
                token_budget=manifest.budget.max_tokens,
                session_token=token,
                trace_callback=self._save_trace,
                epoch_id=manifest.epoch_id,
                observability_store=self._observability_store,
            ))
        except asyncio.TimeoutError as exc:
            raise ProviderFailure(str(exc) or "creator invocation timed out") from exc
        except Exception as exc:
            raise ProviderFailure(f"ADK creator invocation failed: {exc}") from exc
        self._verify_usage_metadata()
        if not finished:
            raise ProviderFailure("creator agent ended without calling finish_candidate")


class AdkSolverBackend(_TraceBackend):
    """A blind ADK solver with read-only bundle tools and explicit submission."""

    def __init__(
        self,
        model: ModelLike,
        *,
        instruction: str = SOLVER_INSTRUCTION,
        require_usage_metadata: bool = True,
        observability_store: Optional[LocalObservabilityStore] = None,
    ) -> None:
        super().__init__(require_usage_metadata, observability_store)
        self.model = model
        self.instruction = instruction
        self.prompt_digest = digest_json(instruction)
        self._last_debrief: Optional[SolverDebrief] = None

    def take_debrief(self) -> Optional[SolverDebrief]:
        debrief = self._last_debrief
        self._last_debrief = None
        return debrief

    def solve(
        self,
        identity: ModelIdentity,
        solver_bundle: Path,
        items: Sequence[Mapping[str, Any]],
        repetition: int,
        manifest: ExperimentManifest,
    ) -> Sequence[Mapping[str, Any]]:
        self._last_debrief = None
        root = Path(solver_bundle).resolve()
        expected_ids = [str(item["id"]) for item in items]
        expected_set = set(expected_ids)
        submitted: Dict[str, Any] = {}
        submitted_debrief: Optional[SolverDebrief] = None
        lock = threading.Lock()

        def list_bundle_files() -> Dict[str, Any]:
            """List every regular file visible in the isolated solver bundle."""
            files = [
                {"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
                for path in _tree_entries(root)
                if path.is_file()
            ]
            return {"files": sorted(files, key=lambda row: row["path"])}

        def read_bundle_file(path: str, offset: int = 0, limit: int = 65536) -> Dict[str, Any]:
            """Read a text or base64-encoded binary bundle file chunk.

            Args:
                path: POSIX path relative to solver_bundle.
                offset: Byte offset at which reading starts.
                limit: Maximum number of bytes to return.
            """
            target = _safe_relative_path(root, path)
            if not target.is_file() or offset < 0 or not 1 <= limit <= 262144:
                raise ValueError("invalid solver bundle read request")
            raw = target.read_bytes()[offset:offset + limit]
            total_bytes = target.stat().st_size
            metadata = {
                "path": path,
                "offset": offset,
                "total_bytes": total_bytes,
                "eof": offset + len(raw) >= total_bytes,
            }
            try:
                return {
                    **metadata,
                    "encoding": "utf-8",
                    "content": raw.decode("utf-8"),
                }
            except UnicodeDecodeError:
                return {
                    **metadata,
                    "encoding": "base64",
                    "content": base64.b64encode(raw).decode("ascii"),
                }

        def submit_predictions(predictions_json: str) -> Dict[str, Any]:
            """Submit the complete prediction array as JSON.

            Args:
                predictions_json: JSON array of objects with exactly id and answer.
            """
            if submitted:
                raise ValueError("predictions are already locked")
            try:
                rows = json.loads(predictions_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"predictions_json is invalid: {exc}") from exc
            if not isinstance(rows, list):
                raise ValueError("predictions_json must be an array")
            parsed: Dict[str, Any] = {}
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"id", "answer"}:
                    raise ValueError("each prediction must contain exactly id and answer")
                item_id = str(row["id"])
                if item_id not in expected_set or item_id in parsed:
                    raise ValueError("prediction IDs must be unique declared item IDs")
                json.dumps(row["answer"], allow_nan=False)
                parsed[item_id] = row["answer"]
            if set(parsed) != expected_set:
                raise ValueError(f"expected {len(expected_set)} complete predictions")
            with lock:
                submitted.clear()
                submitted.update(parsed)
            return {"accepted": len(submitted)}

        def submit_debrief(debrief_json: str) -> Dict[str, Any]:
            """Submit one structured diagnostic after predictions are locked.

            Args:
                debrief_json: JSON object with schema_version 1 and one item diagnostic per prediction.
            """
            nonlocal submitted_debrief
            if set(submitted) != expected_set:
                raise ValueError("submit complete predictions before the debrief")
            if submitted_debrief is not None:
                raise ValueError("the debrief is already locked")
            try:
                value = json.loads(debrief_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"debrief_json is invalid: {exc}") from exc
            if not isinstance(value, dict) or set(value) != {"schema_version", "items"}:
                raise ValueError("debrief_json must contain exactly schema_version and items")
            debrief = solver_debrief_from_mapping(value)
            if {item.item_id for item in debrief.items} != expected_set:
                raise ValueError("debrief IDs must match the locked prediction IDs")
            with lock:
                submitted_debrief = debrief
            return {"accepted": len(debrief.items), "predictions_locked": True}

        agent = Agent(
            name=("solver_" + re.sub(r"\W", "_", identity.artifact_id))[:120],
            model=self.model,
            instruction=self.instruction,
            tools=[
                list_bundle_files,
                read_bundle_file,
                submit_predictions,
                submit_debrief,
            ],
            mode="chat",
        )
        message = json.dumps({
            "epoch_digest": manifest.digest,
            "role": "solver",
            "solver": to_primitive(identity),
            "repetition": repetition,
            "budget": to_primitive(manifest.budget),
            "items": to_primitive(items),
        }, sort_keys=True)
        token = _session_token({
            "epoch": manifest.digest,
            "role": "solver",
            "identity": identity.artifact_id,
            "repetition": repetition,
            "bundle": digest_json(to_primitive(items)),
        })
        try:
            asyncio.run(_run_agent(
                agent=agent,
                role="solver",
                identity=identity,
                message=message,
                timeout_seconds=manifest.budget.solver_seconds,
                max_llm_calls=manifest.budget.max_llm_calls,
                token_budget=manifest.budget.max_tokens,
                session_token=token,
                trace_callback=self._save_trace,
                epoch_id=manifest.epoch_id,
                observability_store=self._observability_store,
            ))
        except asyncio.TimeoutError as exc:
            raise SolverTimedOut(
                f"ADK solver invocation exceeded {manifest.budget.solver_seconds} seconds"
            ) from exc
        except ValueError as exc:
            raise PredictionParseFailure(f"ADK solver submission is invalid: {exc}") from exc
        except Exception as exc:
            raise ProviderFailure(f"ADK solver invocation failed: {exc}") from exc
        self._verify_usage_metadata()
        if set(submitted) != expected_set:
            raise PredictionParseFailure(
                f"solver submitted {len(submitted)} of {len(expected_set)} predictions"
            )
        if submitted_debrief is None:
            raise PredictionParseFailure("solver did not submit the required structured debrief")
        self._last_debrief = submitted_debrief
        return [{"id": item_id, "answer": submitted[item_id]} for item_id in expected_ids]


def resolve_model(identity: ModelIdentity) -> ModelLike:
    """Resolve the exact ADK locator frozen by BBA's internal catalog."""

    return LLMRegistry.new_llm(identity.adk_model)


def build_adk_backends(
    manifest: ExperimentManifest,
    *,
    construction_sandbox: Optional[SecureSandbox] = None,
    creator_instruction: str = CREATOR_INSTRUCTION,
    solver_instruction: str = SOLVER_INSTRUCTION,
    observability_store: Optional[LocalObservabilityStore] = None,
) -> Tuple[Mapping[str, AdkCreatorBackend], Mapping[str, AdkSolverBackend]]:
    """Build the exact creator and solver backend maps required by a controller."""
    configure_gcp_environment(manifest)
    sandbox = construction_sandbox or SecureSandbox()
    if sandbox.backend != manifest.sandbox.backend:
        raise ValueError("construction sandbox does not match the epoch manifest")
    creators = {}
    solvers = {}
    for identity in manifest.cohort:
        model = resolve_model(identity)
        creators[identity.artifact_id] = AdkCreatorBackend(
            model,
            instruction=creator_instruction,
            construction_sandbox=sandbox,
            observability_store=observability_store,
        )
        solvers[identity.artifact_id] = AdkSolverBackend(
            model,
            instruction=solver_instruction,
            observability_store=observability_store,
        )
    return creators, solvers


def build_hidden_solver_backends(
    manifest: ExperimentManifest,
    hidden_panel: Mapping[str, Any],
    *,
    observability_store: Optional[LocalObservabilityStore] = None,
) -> Mapping[str, AdkSolverBackend]:
    """Build the committed sealed-scaffold panel after public closure."""

    from bba.protocol import model_identity_from_mapping

    configure_gcp_environment(manifest)
    scaffold_seed = int(hidden_panel["scaffold_seed"])
    instruction = (
        SOLVER_INSTRUCTION
        + "\nUse the sealed BBA solver scaffold. "
        + f"Scaffold version token: {scaffold_seed:x}."
    )
    result = {}
    public = {item.artifact_id for item in manifest.cohort}
    for value in hidden_panel["models"]:
        identity = model_identity_from_mapping(value)
        if identity.artifact_id in public:
            raise ValueError("hidden solver identity is not distinct from the public panel")
        result[identity.artifact_id] = AdkSolverBackend(
            resolve_model(identity),
            instruction=instruction,
            observability_store=observability_store,
        )
    return result

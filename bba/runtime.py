"""Fail-closed execution boundary for creator-authored code."""

from __future__ import annotations

import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


class SandboxUnavailable(RuntimeError):
    """Raised rather than silently running generated code on the host."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SecureSandbox:
    """Run a command with an OS-enforced filesystem and network boundary.

    macOS uses Seatbelt through ``sandbox-exec``.  Other platforms must supply
    an audited backend; BBA deliberately has no unrestricted fallback.
    """

    def __init__(self, memory_mb: int = 2048, process_limit: int = 64):
        self.memory_mb = memory_mb
        self.process_limit = process_limit
        self.unavailable_reason = ""
        if platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists():
            probe = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default)", "/usr/bin/true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if probe.returncode == 0:
                self.backend = "macos-seatbelt"
            else:
                self.backend = "unavailable"
                self.unavailable_reason = probe.stderr.strip() or "Seatbelt probe failed"
        else:
            self.backend = "unavailable"
            self.unavailable_reason = "no audited sandbox backend is installed"

    @property
    def available(self) -> bool:
        return self.backend != "unavailable"

    def _seatbelt_profile(self, workspace: Path, python: Path) -> str:
        def quoted(path: Path) -> str:
            return str(path).replace("\\", "\\\\").replace('"', '\\"')

        roots = {
            Path("/bin"),
            Path("/usr"),
            Path("/System"),
            Path("/Library/Frameworks"),
            Path("/private/var/db/timezone"),
            python.parent,
        }
        read_rules = "\n".join(
            f'    (subpath "{quoted(path)}")' for path in sorted(roots, key=str) if path.exists()
        )
        return f'''(version 1)
(deny default)
(allow process-fork)
(allow process-exec
{read_rules})
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup)
(allow file-read-metadata)
(allow file-read*
{read_rules}
    (subpath "{quoted(workspace)}"))
(allow file-write* (subpath "{quoted(workspace)}"))
(deny network*)
'''

    def run(
        self,
        command: List[str],
        workspace: Path,
        timeout_seconds: int,
        cwd: Optional[Path] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        workspace = Path(workspace).resolve()
        if not workspace.is_dir() or workspace.is_symlink():
            raise ValueError("sandbox workspace must be a real directory")
        if not self.available:
            raise SandboxUnavailable(
                "no audited generated-code sandbox is available: " + self.unavailable_reason
            )
        python = Path(sys.executable).resolve()
        profile_path = workspace / ".bba-seatbelt.sb"
        profile_path.write_text(self._seatbelt_profile(workspace, python), encoding="utf-8")
        wrapped = ["/usr/bin/sandbox-exec", "-f", str(profile_path), "--"] + list(command)
        home = workspace / ".home"
        temporary = workspace / ".tmp"
        home.mkdir(exist_ok=True)
        temporary.mkdir(exist_ok=True)
        clean_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
        }
        if env_overrides:
            prohibited = {"HOME", "PYTHONPATH", "TMPDIR"}.intersection(env_overrides)
            if prohibited:
                raise ValueError(f"cannot override protected sandbox variables: {sorted(prohibited)}")
            clean_env.update(env_overrides)

        def limits() -> None:
            os.setsid()
            memory = self.memory_mb * 1024 * 1024
            requested = (
                (resource.RLIMIT_AS, memory),
                (resource.RLIMIT_NPROC, self.process_limit),
                (resource.RLIMIT_FSIZE, 512 * 1024 * 1024),
            )
            for limit_name, value in requested:
                try:
                    _soft, hard = resource.getrlimit(limit_name)
                    applied = value if hard == resource.RLIM_INFINITY else min(value, hard)
                    resource.setrlimit(limit_name, (applied, applied))
                except (OSError, ValueError):
                    # Seatbelt and the controller timeout remain mandatory; a
                    # platform may not support every supplementary rlimit.
                    continue

        process = subprocess.Popen(
            wrapped,
            cwd=str((cwd or workspace).resolve()),
            env=clean_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=limits,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return CommandResult(process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            return CommandResult(-1, stdout, stderr, timed_out=True)

    def run_python(
        self,
        script: Path,
        args: List[str],
        workspace: Path,
        timeout_seconds: int,
        cwd: Optional[Path] = None,
    ) -> CommandResult:
        return self.run(
            [str(Path(sys.executable).resolve()), str(Path(script).resolve())] + list(args),
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )

"""Fail-closed execution boundary for creator-authored code."""

from __future__ import annotations

import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


class SandboxUnavailable(RuntimeError):
    """Raised rather than silently running generated code on the host."""


def expected_sandbox_backend() -> str:
    """Return the sandbox identity that an epoch must freeze on this host."""

    operating_system = platform.system()
    if operating_system == "Darwin":
        return "macos-seatbelt"
    if operating_system == "Linux":
        return "linux-bubblewrap"
    raise SandboxUnavailable(
        f"BBA does not support a generated-code sandbox on {operating_system or 'this OS'}"
    )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SecureSandbox:
    """Run a command with an OS-enforced filesystem and network boundary.

    macOS hosts use Seatbelt. Linux hosts use Bubblewrap. BBA has no
    unrestricted or hosted fallback.
    """

    def __init__(
        self,
        memory_mb: int = 2048,
        process_limit: int = 64,
        cpu_seconds: int = 600,
        file_size_mb: int = 512,
    ):
        self.memory_mb = memory_mb
        self.process_limit = process_limit
        self.cpu_seconds = cpu_seconds
        self.file_size_mb = file_size_mb
        self.unavailable_reason = ""
        self.backend = "unavailable"
        try:
            self.expected_backend = expected_sandbox_backend()
        except SandboxUnavailable as exc:
            self.expected_backend = "unsupported"
            self.unavailable_reason = str(exc)

        if self.expected_backend == "macos-seatbelt" and Path("/usr/bin/sandbox-exec").exists():
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
                self.unavailable_reason = probe.stderr.strip() or "Seatbelt probe failed"
        elif self.expected_backend == "macos-seatbelt":
            self.unavailable_reason = "/usr/bin/sandbox-exec is not installed"
        elif self.expected_backend == "linux-bubblewrap":
            self._bwrap = shutil.which("bwrap")
            if self._bwrap is None:
                self.unavailable_reason = "Bubblewrap is not installed; install the bubblewrap package"
            else:
                self._probe_bubblewrap()
        required = ("RLIMIT_AS", "RLIMIT_NPROC", "RLIMIT_FSIZE", "RLIMIT_CPU")
        missing = [name for name in required if not hasattr(resource, name)]
        if self.available and missing:
            self.backend = "unavailable"
            self.unavailable_reason = "mandatory resource controls are unavailable: " + ", ".join(missing)

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
(allow file-read*
{read_rules}
    (subpath "{quoted(workspace)}"))
(allow file-write* (subpath "{quoted(workspace)}"))
(deny network*)
'''

    @staticmethod
    def _path_is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _linux_runtime_mounts(self) -> list[tuple[Path, Path]]:
        """Return the read-only runtime paths needed to start local Python."""

        mounts: list[tuple[Path, Path]] = []
        for path in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"), Path("/sbin")):
            if path.exists():
                mounts.append((path, path))

        for prefix in (Path(sys.base_prefix).resolve(), Path(sys.executable).resolve().parent):
            if prefix.exists() and not any(
                self._path_is_within(prefix, destination)
                for _source, destination in mounts
            ):
                mounts.append((prefix, prefix))

        for path in (
            Path("/etc/ld.so.cache"),
            Path("/etc/ld.so.conf"),
            Path("/etc/ld.so.conf.d"),
            Path("/etc/localtime"),
        ):
            if path.exists():
                mounts.append((path, path))

        unique: dict[str, tuple[Path, Path]] = {}
        for source, destination in mounts:
            unique[str(destination)] = (source, destination)
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _namespace_parent_directories(paths: list[Path]) -> list[Path]:
        """Return missing namespace parents for bind-mount destinations."""

        directories: set[Path] = set()
        for path in paths:
            current = path.parent
            while current != Path("/"):
                directories.add(current)
                current = current.parent
        return sorted(directories, key=lambda value: (len(value.parts), str(value)))

    def _bubblewrap_command(
        self,
        command: List[str],
        workspace: Path,
        selected_cwd: Path,
        clean_env: Dict[str, str],
    ) -> List[str]:
        if not getattr(self, "_bwrap", None):
            raise SandboxUnavailable("Bubblewrap is not installed")

        runtime_mounts = self._linux_runtime_mounts()
        destinations = [destination for _source, destination in runtime_mounts]
        occupied_directories = [path for path in destinations if path.is_dir()]
        namespace_roots = {Path("/tmp"), Path("/proc"), Path("/dev")}
        parents = []
        for parent in self._namespace_parent_directories(destinations + [workspace]):
            if parent in namespace_roots:
                continue
            if any(self._path_is_within(parent, root) for root in occupied_directories):
                continue
            parents.append(parent)

        wrapped = [
            str(self._bwrap),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--cap-drop",
            "ALL",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for parent in parents:
            wrapped.extend(("--dir", str(parent)))
        for source, destination in runtime_mounts:
            wrapped.extend(("--ro-bind", str(source), str(destination)))
        wrapped.extend(("--bind", str(workspace), str(workspace)))
        wrapped.extend(("--chdir", str(selected_cwd), "--clearenv"))
        for name, value in sorted(clean_env.items()):
            wrapped.extend(("--setenv", name, value))
        wrapped.append("--")
        wrapped.extend(command)
        return wrapped

    def _probe_bubblewrap(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="bba-bwrap-probe-") as temporary:
                workspace = Path(temporary).resolve()
                (workspace / ".home").mkdir()
                (workspace / ".tmp").mkdir()
                command = self._bubblewrap_command(
                    ["/usr/bin/true"],
                    workspace,
                    workspace,
                    {
                        "PATH": "/usr/bin:/bin",
                        "HOME": str(workspace / ".home"),
                        "TMPDIR": str(workspace / ".tmp"),
                        "LANG": "C.UTF-8",
                    },
                )
                probe = subprocess.run(
                    command,
                    cwd=str(workspace),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
            if probe.returncode == 0:
                self.backend = "linux-bubblewrap"
            else:
                self.unavailable_reason = probe.stderr.strip() or "Bubblewrap namespace probe failed"
        except (OSError, subprocess.SubprocessError) as exc:
            self.unavailable_reason = f"Bubblewrap namespace probe failed: {exc}"

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
            unsupported = set(env_overrides).difference({"PYTHONPATH"})
            if unsupported:
                raise ValueError(f"unsupported sandbox environment overrides: {sorted(unsupported)}")
            python_path = env_overrides.get("PYTHONPATH")
            if python_path is not None:
                dependency_root = Path(python_path).resolve()
                try:
                    dependency_root.relative_to(workspace)
                except ValueError as exc:
                    raise ValueError("sandbox PYTHONPATH must stay in the workspace") from exc
            clean_env.update(env_overrides)

        selected_cwd = (cwd or workspace).resolve()
        try:
            selected_cwd.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("sandbox working directory must stay in the workspace") from exc
        if self.backend == "macos-seatbelt":
            python = Path(sys.executable).resolve()
            profile_path = workspace / ".bba-seatbelt.sb"
            profile_path.write_text(self._seatbelt_profile(workspace, python), encoding="utf-8")
            wrapped = ["/usr/bin/sandbox-exec", "-f", str(profile_path), "--"] + list(command)
        elif self.backend == "linux-bubblewrap":
            wrapped = self._bubblewrap_command(
                list(command), workspace, selected_cwd, clean_env
            )
        else:  # pragma: no cover - guarded by available above
            raise SandboxUnavailable("unsupported generated-code sandbox backend")

        def limits() -> None:
            os.setsid()
            memory = self.memory_mb * 1024 * 1024
            requested = (
                (resource.RLIMIT_AS, memory),
                (resource.RLIMIT_NPROC, self.process_limit),
                (resource.RLIMIT_FSIZE, self.file_size_mb * 1024 * 1024),
                (resource.RLIMIT_CPU, min(self.cpu_seconds, timeout_seconds)),
            )
            for limit_name, value in requested:
                try:
                    _soft, hard = resource.getrlimit(limit_name)
                    applied = value if hard == resource.RLIM_INFINITY else min(value, hard)
                    resource.setrlimit(limit_name, (applied, applied))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"cannot apply mandatory resource limit {limit_name}") from exc

        process = subprocess.Popen(
            wrapped,
            cwd=str(selected_cwd),
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
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        return self.run(
            [str(Path(sys.executable).resolve()), str(Path(script).resolve())] + list(args),
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            env_overrides=env_overrides,
        )

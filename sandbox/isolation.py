"""POSIX scratchpad execution environment with clean isolation and timeout enforcement."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ScratchpadEnvironment:
    """Isolated scratchpad directory and process executor."""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        prefix: str = "bba_sandbox_",
        auto_clean: bool = True,
    ):
        self.base_dir: Optional[str] = base_dir
        self.prefix: str = prefix
        self.auto_clean: bool = auto_clean
        self._path: Optional[str] = None

    @property
    def path(self) -> str:
        if self._path is None:
            self._path = tempfile.mkdtemp(prefix=self.prefix, dir=self.base_dir)
        return self._path

    def __enter__(self) -> "ScratchpadEnvironment":
        _ = self.path
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.auto_clean:
            self.cleanup()

    def cleanup(self) -> None:
        if self._path and os.path.exists(self._path):
            try:
                shutil.rmtree(self._path, ignore_errors=True)
            except Exception:
                pass
            self._path = None

    def run_command(
        self,
        cmd: List[str],
        timeout_seconds: int = 30,
        env_overrides: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Executes a command in isolated process group with strict timeout enforcement."""
        current_pythonpath = os.environ.get("PYTHONPATH", "")
        workspace_dir = os.path.abspath(os.getcwd())
        combined_pythonpath = f"{self.path}:{workspace_dir}:{current_pythonpath}".strip(":")

        clean_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": self.path,
            "HOME": self.path,
            "PYTHONPATH": combined_pythonpath,
        }
        if env_overrides:
            clean_env.update(env_overrides)

        working_dir = cwd or self.path
        is_posix = os.name == "posix"
        preexec = os.setsid if is_posix else None

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=working_dir,
                env=clean_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=preexec,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                if is_posix:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        proc.kill()
                else:
                    proc.kill()
                stdout, stderr = proc.communicate()
                return -1, stdout, f"Process timed out after {timeout_seconds} seconds.\n{stderr}"
        except Exception as e:
            return -1, "", f"Failed to execute command {cmd}: {e}"

    def run_python_code(
        self,
        script_path_or_code: str,
        args: Optional[List[str]] = None,
        timeout_seconds: int = 30,
        env_overrides: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Executes a Python script or Python code string inside the scratchpad."""
        args = args or []
        script_file: str

        if os.path.isfile(script_path_or_code):
            script_file = os.path.abspath(script_path_or_code)
            working_dir = cwd or os.path.dirname(script_file)
        else:
            temp_script = os.path.join(self.path, "_temp_run_script.py")
            with open(temp_script, "w", encoding="utf-8") as f:
                f.write(script_path_or_code)
            script_file = temp_script
            working_dir = cwd or self.path

        cmd = [sys.executable, script_file] + args
        return self.run_command(
            cmd=cmd,
            timeout_seconds=timeout_seconds,
            env_overrides=env_overrides,
            cwd=working_dir,
        )

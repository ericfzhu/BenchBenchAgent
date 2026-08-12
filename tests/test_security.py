"""Executable security checks for the supported generated-code sandbox."""

from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path

from bba.runtime import SecureSandbox


@unittest.skipUnless(SecureSandbox().available, "audited local sandbox is unavailable")
class TestSecureSandboxConformance(unittest.TestCase):
    def setUp(self):
        self.sandbox = SecureSandbox(memory_mb=256, process_limit=64, cpu_seconds=3)
        self.temporary = tempfile.TemporaryDirectory(prefix="bba-security-")
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def run_python(self, source: str, timeout: int = 5):
        script = self.workspace / "probe.py"
        script.write_text(source, encoding="utf-8")
        return self.sandbox.run_python(
            script, [], self.workspace, timeout_seconds=timeout
        )

    def test_unrelated_host_content_and_metadata_are_denied(self):
        protected = Path(self.temporary.name) / "protected.txt"
        protected.write_text("controller secret", encoding="utf-8")
        result = self.run_python(
            f"import os\nprint(os.stat({str(protected)!r}))\n"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_controller_credentials_are_denied(self):
        protected = Path(self.temporary.name) / "application_default_credentials.json"
        protected.write_text('{"private_key": "controller secret"}', encoding="utf-8")
        result = self.run_python(
            "import os\n"
            "assert 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ\n"
            f"open({str(protected)!r}, 'rb').read()\n"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_network_namespace_is_denied(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = self.run_python(
                "import socket\n"
                f"socket.create_connection(('127.0.0.1', {port}), timeout=1)\n"
            )
        self.assertNotEqual(result.returncode, 0)

    def test_home_and_tmp_are_ephemeral(self):
        result = self.run_python(
            "import os\n"
            "assert os.environ['HOME'].endswith('.home')\n"
            "assert os.environ['TMPDIR'].endswith('.tmp')\n"
            "open(os.path.join(os.environ['HOME'], 'ok'), 'w').write('ok')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wall_clock_timeout_terminates_process_group(self):
        result = self.run_python(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n",
            timeout=1,
        )
        self.assertTrue(result.timed_out)

    def test_cpu_limit_stops_busy_process(self):
        result = self.run_python("while True:\n    pass\n", timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)

    def test_memory_limit_rejects_large_allocation(self):
        result = self.run_python(
            "try:\n"
            "    value = bytearray(1024 * 1024 * 1024)\n"
            "except MemoryError:\n"
            "    print('MEMORY_LIMIT')\n"
            "else:\n"
            "    raise SystemExit('memory limit did not apply')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MEMORY_LIMIT", result.stdout)

    def test_process_limit_rejects_a_fork_burst(self):
        self.sandbox = SecureSandbox(
            memory_mb=256, process_limit=16, cpu_seconds=3
        )
        result = self.run_python(
            "import os, signal\n"
            "children = []\n"
            "limited = False\n"
            "try:\n"
            "    for _ in range(64):\n"
            "        pid = os.fork()\n"
            "        if pid == 0:\n"
            "            os.pause()\n"
            "        children.append(pid)\n"
            "except OSError:\n"
            "    limited = True\n"
            "finally:\n"
            "    for pid in children:\n"
            "        os.kill(pid, signal.SIGKILL)\n"
            "    for pid in children:\n"
            "        os.waitpid(pid, 0)\n"
            "raise SystemExit(0 if limited else 'process limit did not apply')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_file_size_limit_rejects_large_output(self):
        self.sandbox = SecureSandbox(
            memory_mb=256, process_limit=64, cpu_seconds=3, file_size_mb=1
        )
        result = self.run_python(
            "try:\n"
            "    open('large.bin', 'wb').write(b'x' * (2 * 1024 * 1024))\n"
            "except OSError:\n"
            "    print('FILE_LIMIT')\n"
            "else:\n"
            "    raise SystemExit('file limit did not apply')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FILE_LIMIT", result.stdout)

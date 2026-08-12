"""Executable security checks for the supported generated-code sandbox."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bba.runtime import SecureSandbox


@unittest.skipUnless(SecureSandbox().available, "audited macOS sandbox is unavailable")
class TestSecureSandboxConformance(unittest.TestCase):
    def setUp(self):
        self.sandbox = SecureSandbox(memory_mb=256, process_limit=8, cpu_seconds=2)
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

    def test_network_and_adc_are_denied(self):
        result = self.run_python(
            "import os, socket\n"
            "assert 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ\n"
            "socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
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

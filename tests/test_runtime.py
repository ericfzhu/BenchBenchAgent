"""Platform selection and command contract tests for local sandboxes."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from bba.runtime import SandboxUnavailable, SecureSandbox, expected_sandbox_backend


class TestSandboxPlatformContract(unittest.TestCase):
    def test_platform_selects_the_expected_backend(self):
        with patch("bba.runtime.platform.system", return_value="Darwin"):
            self.assertEqual(expected_sandbox_backend(), "macos-seatbelt")
        with patch("bba.runtime.platform.system", return_value="Linux"):
            self.assertEqual(expected_sandbox_backend(), "linux-bubblewrap")
        with patch("bba.runtime.platform.system", return_value="Windows"):
            with self.assertRaises(SandboxUnavailable):
                expected_sandbox_backend()

    def test_bubblewrap_command_has_a_closed_namespace(self):
        sandbox = object.__new__(SecureSandbox)
        sandbox._bwrap = "/usr/bin/bwrap"
        sandbox._linux_runtime_mounts = lambda: [
            (Path("/usr"), Path("/usr")),
            (Path("/etc/ld.so.cache"), Path("/etc/ld.so.cache")),
        ]
        workspace = Path("/tmp/bba-test/workspace")
        command = sandbox._bubblewrap_command(
            ["/usr/bin/python3", "probe.py"],
            workspace,
            workspace,
            {"PATH": "/usr/bin:/bin"},
        )
        self.assertIn("--unshare-all", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("--clearenv", command)
        self.assertIn("--die-with-parent", command)
        self.assertIn("--tmpfs", command)
        self.assertIn("/tmp/bba-test", command)
        bind_index = command.index("--bind")
        self.assertEqual(command[bind_index + 1:bind_index + 3], [str(workspace)] * 2)
        self.assertNotIn("/home", command)

    def test_environment_overrides_are_controller_owned(self):
        sandbox = object.__new__(SecureSandbox)
        sandbox.backend = "linux-bubblewrap"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "environment overrides"):
                sandbox.run(
                    ["/usr/bin/true"], Path(temporary), 1,
                    env_overrides={"LD_PRELOAD": "/tmp/host-library.so"},
                )

    def test_pythonpath_must_stay_in_the_workspace(self):
        sandbox = object.__new__(SecureSandbox)
        sandbox.backend = "linux-bubblewrap"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "PYTHONPATH"):
                sandbox.run(
                    ["/usr/bin/true"], Path(temporary), 1,
                    env_overrides={"PYTHONPATH": "/tmp/host-dependencies"},
                )


if __name__ == "__main__":
    unittest.main()

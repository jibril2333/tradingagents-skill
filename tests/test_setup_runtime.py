"""Interpreter discovery in setup_runtime.py; no network and no real installs."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "tradingagents" / "scripts"))

import setup_runtime  # noqa: E402


class FindInterpreterTest(unittest.TestCase):
    def test_uses_the_running_interpreter_when_new_enough(self):
        with mock.patch.object(setup_runtime.sys, "version_info", (3, 12, 0)):
            self.assertEqual(setup_runtime.find_interpreter(), [sys.executable])

    def test_old_interpreter_picks_the_first_suitable_candidate(self):
        candidates = [["/usr/bin/python3.13"], ["/opt/homebrew/bin/python3.12"], ["/usr/bin/python3"]]
        with mock.patch.object(setup_runtime.sys, "version_info", (3, 9, 6)), \
                mock.patch.object(setup_runtime, "candidate_commands", return_value=iter(candidates)), \
                mock.patch.object(setup_runtime, "version_ok", side_effect=lambda c: c[0].endswith("3.12")):
            self.assertEqual(setup_runtime.find_interpreter(), ["/opt/homebrew/bin/python3.12"])

    def test_candidates_include_homebrew_and_windows_launcher(self):
        with mock.patch.object(setup_runtime.shutil, "which", return_value=None), \
                mock.patch.object(setup_runtime, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(setup_runtime.Path, "is_file", return_value=True):
            commands = list(setup_runtime.candidate_commands())
        self.assertEqual(commands[0], ["py", "-3.13"])
        self.assertIn([str(Path("/opt/homebrew/bin") / "python3.12")], commands)


class CreateEnvTest(unittest.TestCase):
    def test_found_interpreter_creates_a_venv(self):
        with mock.patch.object(setup_runtime, "find_interpreter", return_value=["/x/python3.12"]), \
                mock.patch.object(setup_runtime.subprocess, "run") as run:
            setup_runtime.create_env(Path("/tmp/env"))
        run.assert_called_once_with(["/x/python3.12", "-m", "venv", str(Path("/tmp/env"))], check=True)

    def test_falls_back_to_uv_with_a_managed_python(self):
        with mock.patch.object(setup_runtime, "find_interpreter", return_value=None), \
                mock.patch.object(setup_runtime.shutil, "which", return_value="/bin/uv"), \
                mock.patch.object(setup_runtime.subprocess, "run") as run:
            setup_runtime.create_env(Path("/tmp/env"))
        run.assert_called_once_with(["/bin/uv", "venv", "--seed", "--python", "3.12", str(Path("/tmp/env"))],
                                    check=True)

    def test_no_interpreter_and_no_uv_explains_the_options(self):
        with mock.patch.object(setup_runtime, "find_interpreter", return_value=None), \
                mock.patch.object(setup_runtime.shutil, "which", return_value=None), \
                self.assertRaises(SystemExit) as ctx:
            setup_runtime.create_env(Path("/tmp/env"))
        self.assertIn("python.org", str(ctx.exception.code))
        self.assertIn("uv", str(ctx.exception.code))


if __name__ == "__main__":
    unittest.main()

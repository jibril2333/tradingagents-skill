"""tools/install_skill.py copies the skill into the chosen host's skill directory."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallSkillTest(unittest.TestCase):
    def run_installer(self, home, *args):
        # Keep the rest of the environment: Windows needs SYSTEMROOT to start Python.
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        return subprocess.run([sys.executable, str(ROOT / "tools" / "install_skill.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_hosts_use_their_user_skill_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for host, parent in (("claude-code", home / ".claude" / "skills"),
                                 ("codex", home / ".agents" / "skills")):
                result = self.run_installer(home, "--host", host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((parent / "tradingagents" / "SKILL.md").is_file())
                self.assertTrue((parent / "tradingagents" / "scripts" / "ta.py").is_file())

    def test_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(self.run_installer(home, "--host", "codex").returncode, 0)
            self.assertNotEqual(self.run_installer(home, "--host", "codex").returncode, 0)


if __name__ == "__main__":
    unittest.main()

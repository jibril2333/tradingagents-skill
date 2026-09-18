"""Copy this skill to a skill discovery directory without overwriting."""

import argparse
import os
from pathlib import Path
import shutil
import stat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path.home() / ".claude" / "skills",
                        help="Skill parent directory (default: ~/.claude/skills)")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "skills" / "tradingagents"
    target = args.dest.expanduser().resolve() / "tradingagents"
    if target.exists():
        parser.error(f"Destination exists; no files changed: {target}")
    if source == target or source in target.parents:
        parser.error("Destination cannot be inside the source skill.")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if os.name != "nt":
        launcher = target / "scripts" / "ta"
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed: {target}")


if __name__ == "__main__":
    main()

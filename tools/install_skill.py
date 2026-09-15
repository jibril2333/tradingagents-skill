"""Copy this skill to a chosen discovery directory without overwriting."""

import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True,
                        help="Skill parent directory, e.g. PROJECT/.agents/skills")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "skills" / "tradingagents"
    target = args.dest.expanduser().resolve() / "tradingagents"
    if target.exists():
        parser.error(f"Destination exists; no files changed: {target}")
    if source == target or source in target.parents:
        parser.error("Destination cannot be inside the source skill.")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Installed: {target}")


if __name__ == "__main__":
    main()

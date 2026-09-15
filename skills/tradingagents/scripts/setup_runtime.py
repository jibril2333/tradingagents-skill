"""Install the reviewed upstream revision into an explicit virtual environment."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-dir", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required (3.12 recommended).")
    target = args.env_dir.expanduser().resolve()
    if target.exists() and not (target / "pyvenv.cfg").is_file():
        parser.error("Target exists and is not a virtual environment; choose a new path.")
    if not target.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = Path(__file__).with_name("requirements.txt")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(requirements)], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    print(f"Runtime ready: {python}")


if __name__ == "__main__":
    main()

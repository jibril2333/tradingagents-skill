"""Install the reviewed upstream revision into an explicit virtual environment.

Runs under any Python 3. When the interpreter running it is older than 3.10
(macOS ships 3.9 as python3), it looks for a newer one on the machine, and
falls back to uv, which downloads a managed Python, before giving up.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

MIN_VERSION = (3, 10)
CANDIDATE_MINORS = (13, 12, 11, 10)
EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
UV_PYTHON = "3.12"
NO_INTERPRETER = """No Python 3.10+ interpreter was found, and uv is not installed.
Install one of the following, then run this script again:
  - Python 3.12 from https://www.python.org/downloads/ (or `brew install python@3.12` on macOS)
  - uv from https://docs.astral.sh/uv/ ; this script then downloads Python 3.12 by itself"""


def version_ok(command):
    """Whether ``command`` starts a Python interpreter of at least MIN_VERSION."""
    check = "import sys; sys.exit(0 if sys.version_info >= (%d, %d) else 1)" % MIN_VERSION
    try:
        return subprocess.run([*command, "-c", check], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def candidate_commands():
    """Interpreters to try, newest first."""
    if os.name == "nt":
        for minor in CANDIDATE_MINORS:
            yield ["py", f"-3.{minor}"]
    for minor in CANDIDATE_MINORS:
        name = f"python3.{minor}"
        found = shutil.which(name)
        if found:
            yield [found]
        for directory in EXTRA_DIRS:
            path = Path(directory) / name
            if path.is_file():
                yield [str(path)]
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            yield [found]


def find_interpreter():
    """A command for a suitable Python, preferring the one running this script."""
    if sys.version_info >= MIN_VERSION:
        return [sys.executable]
    return next((command for command in candidate_commands() if version_ok(command)), None)


def create_env(target: Path):
    interpreter = find_interpreter()
    if interpreter is not None:
        subprocess.run([*interpreter, "-m", "venv", str(target)], check=True)
        return
    uv = shutil.which("uv")
    if uv is None:
        sys.exit(NO_INTERPRETER)
    print(f"No local Python 3.10+ found; creating the environment with uv and Python {UV_PYTHON}.")
    subprocess.run([uv, "venv", "--seed", "--python", UV_PYTHON, str(target)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-dir", type=Path, default=Path.home() / ".tradingagents-skill" / "venv",
                        help="Virtual environment path (default: ~/.tradingagents-skill/venv)")
    args = parser.parse_args()
    target = args.env_dir.expanduser().resolve()
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if target.exists():
        if not (target / "pyvenv.cfg").is_file():
            parser.error("Target exists and is not a virtual environment; choose a new path.")
        if not version_ok([str(python)]):
            parser.error(f"The existing environment at {target} is older than Python 3.10; "
                         "remove it or choose a new --env-dir.")
    else:
        create_env(target)
    requirements = Path(__file__).with_name("requirements.txt")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(requirements)], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    print(f"Runtime ready: {python}")


if __name__ == "__main__":
    main()

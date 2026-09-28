"""C syntax-check script

Compiles the given C files with `-fsyntax-only`, reusing the per-file flags the
Meson build records in `compile_commands.json`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Its flags cover every include and dependency the extension uses, so files the
# build does not compile (its headers) are checked with them
REPRESENTATIVE = ROOT / "src/compresso/csrc/_core.c"

# The C tests include by bare name through their own build's include paths, so
# they are checked with that build's flags, and their headers with a test's
TESTS_DIR = ROOT / "tests/c"
TESTS_DATABASE = ROOT / "build/c-tests/compile_commands.json"
TESTS_REPRESENTATIVE = TESTS_DIR / "test_codec.c"

# Flags that produce output, each with the number of values that follow it
OUTPUT_FLAGS = {"-o": 1, "-c": 0, "-MD": 0, "-MMD": 0, "-MQ": 1, "-MT": 1, "-MF": 1}


def find_database() -> Path | None:
    """Find the most recently configured extension build's compile database.

    Only the per-interpreter `build/cp*` dirs meson-python creates count; the C
    test build beside them never compiles `_core.c`.

    Returns:
        The newest `build/cp*/compile_commands.json`, or None if no build dir
        has been configured yet.
    """
    candidates = list(ROOT.glob("build/cp*/compile_commands.json"))
    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_commands(database: Path) -> dict[Path, tuple[Path, list[str]]]:
    """Map each compiled source to its working dir and syntax-only command.

    Args:
        database: Path to a `compile_commands.json`.

    Returns:
        `{source: (directory, argv)}`, where `argv` is the recorded command
        with its outputs and source removed.
    """
    commands: dict[Path, tuple[Path, list[str]]] = {}
    for entry in json.loads(database.read_text()):
        directory = Path(entry["directory"])
        argv = entry.get("arguments") or shlex.split(entry["command"])

        stripped: list[str] = []
        skip = 0
        for arg in argv:
            if skip:
                skip -= 1
            elif arg in OUTPUT_FLAGS:
                skip = OUTPUT_FLAGS[arg]
            elif arg != entry["file"]:
                stripped.append(arg)

        source = (directory / entry["file"]).resolve()
        commands[source] = (directory, stripped)

    return commands


def check(paths: list[Path], commands: dict[Path, tuple[Path, list[str]]]) -> int:
    """Syntax-check `paths`.

    Args:
        paths: C sources or headers to check.
        commands: The compile database, as returned by `load_commands`.

    Returns:
        The first non-zero compiler exit code, or 0 when every file passes.
    """
    fallback = commands.get(REPRESENTATIVE) or next(iter(commands.values()))
    tests_fallback = commands.get(TESTS_REPRESENTATIVE)

    # Every source in a target shares its flags, so one run per distinct
    # command checks them all
    groups: dict[tuple[Path, tuple[str, ...]], list[str]] = {}
    for path in paths:
        resolved = path.resolve()
        if resolved.is_relative_to(TESTS_DIR):
            if tests_fallback is None:
                print(f"check_c_syntax: skipping {path}; run `just test-c` first")
                continue
            directory, argv = commands.get(resolved, tests_fallback)

        else:
            directory, argv = commands.get(resolved, fallback)
        groups.setdefault((directory, tuple(argv)), []).append(str(path.resolve()))

    returncode = 0
    for (directory, argv), files in groups.items():
        cmd = [*argv, "-fsyntax-only", *files]
        result = subprocess.run(cmd, cwd=directory, check=False)
        returncode = returncode or result.returncode

    return returncode


if __name__ == "__main__":
    database = find_database()
    if database is None:
        print(
            "check_c_syntax: no configured Meson build dir, run `just install`; "
            "skipping C syntax check"
        )
        sys.exit(0)

    extension = load_commands(database)
    tests = load_commands(TESTS_DATABASE) if TESTS_DATABASE.is_file() else {}

    # The tests' build compiles the csrc sources too; the extension's own flags
    # are the ones to check those with
    commands = {**tests, **extension}

    # Check every source and header the extension builds if no arguments are given
    args = [Path(arg) for arg in sys.argv[1:]]
    headers = sorted((ROOT / "src/compresso/csrc").rglob("*.h"))
    sys.exit(check(args or [*extension, *headers], commands))

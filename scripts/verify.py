"""Runs every local check a change needs and prints one line per stage.

Usage:
    python scripts/verify.py                    # every stage
    python scripts/verify.py --changed          # only stages the uncommitted changes affect
    python scripts/verify.py --only build,pytest
    python scripts/verify.py --skip asan
    python scripts/verify.py --fix              # apply ruff's formatting and fixes
    python scripts/verify.py --record           # re-record the codec manifest

Stages run concurrently, each once the stages it depends on have finished, and
are reported as they finish. Every stage runs even after one fails. Each
stage's full output is saved under `build/verify/`, and a failing stage's
diagnostics (or last lines) are printed after the summary.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path

from check_c_syntax import TESTS_DIR, find_database, load_commands

ROOT = Path(__file__).resolve().parent.parent
CSRC = ROOT / "src/compresso/csrc"
LOG_DIR = ROOT / "build/verify"
BIN = Path(sys.executable).parent
TAIL_LINES = 40

ASAN_ENV = {
    "ASAN_OPTIONS": "detect_leaks=0",
    "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
}

# The build records `-fdiagnostics-color=always`, so compiler output carries
# colour codes even when captured
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
COMPILER_DIAGNOSTIC = re.compile(r"\b(warning|error):")
MESON_COUNT = re.compile(
    r"^(Ok|Expected Fail|Fail|Unexpected Pass|Skipped|Timeout):\s+(\d+)", re.MULTILINE
)
PYTEST_SUMMARY = re.compile(r"^(?:=+ )?(\d+ \w+(?:, \d+ \w+)*) in [\d.]+s")

C_STAGES = {"build", "c-tests", "asan", "syntax", "codec", "pytest"}
PY_STAGES = {"format", "lint", "type", "pytest"}
IGNORED_SUFFIXES = {".md"}

# `codec` and `pytest` import the editable extension, whose import can rebuild
# the dir `build` is compiling, and `syntax` reads the compile databases that
# `build` and `c-tests` (re)generate
DEPENDENCIES = {
    "codec": {"build"},
    "syntax": {"build", "c-tests"},
    "pytest": {"build", "codec", "syntax"},
}

# With --fix, ruff rewrites Python files, so nothing that reads them may overlap
FIX_DEPENDENCIES = {
    "lint": {"format"},
    "syntax": {"format", "lint"},
    "codec": {"format", "lint"},
    "type": {"format", "lint"},
    "pytest": {"format", "lint"},
}

if not os.environ.get("NO_COLOR") and (
    sys.stdout.isatty() or os.environ.get("FORCE_COLOR")
):
    # ANSI color codes for pass/fail indicators
    PASS = "\033[92m" + "✔" + "\033[0m"
    FAIL = "\033[91m" + "✘" + "\033[0m"
    SKIP = "\033[93m" + "➜" + "\033[0m"
else:
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class Result:
    """Class representing the result of a verification stage."""

    status: str
    detail: str = ""
    output: str = ""
    diagnostics: list[str] = field(default_factory=list)


def tool(name: str) -> str:
    """Prefer the project env's copy of a tool over whatever is on PATH.

    Args:
        name: The name of the tool to find.

    Returns:
        The full path to the tool, or the name if not found.
    """
    return shutil.which(name, path=str(BIN)) or name


def run(*cmd: str | Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a command from the repo root.

    Args:
        cmd: The command to run, as a sequence of strings or Path objects.
        env: Optional environment variables to set.

    Returns:
        The exit code and the combined stdout and stderr, without colour codes.
    """
    proc = subprocess.run(
        [str(arg) for arg in cmd],
        cwd=ROOT,
        env={**os.environ, **env} if env else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    return proc.returncode, ANSI_ESCAPE.sub("", proc.stdout)


def matching(output: str, pattern: re.Pattern[str]) -> list[str]:
    """Find all lines in the output that match the given pattern.

    Args:
        output: The output string to search.
        pattern: The regex pattern to match.

    Returns:
        A list of unique lines that match the pattern.
    """
    return list(
        dict.fromkeys(line for line in output.splitlines() if pattern.search(line))
    )


def last_line(output: str) -> str:
    """Return the last non-empty line of the output.

    Args:
        output: The output string to search.

    Returns:
        The last non-empty line, or an empty string if none found.
    """
    lines = [line for line in output.splitlines() if line.strip()]

    return lines[-1].strip() if lines else ""


def compile_result(code: int, output: str, label: str) -> Result:
    """Fail a compile on any warning, as CI's `-Dwerror=true` builds do.

    Args:
        code: The return code of the compile process.
        output: The output string from the compile process.
        label: A label for the compile result.

    Returns:
        A Result object representing the compile result.
    """
    diagnostics = matching(output, COMPILER_DIAGNOSTIC)
    warnings = sum("warning:" in line for line in diagnostics)
    if code:
        return Result(FAIL, f"{label}: build failed", output, diagnostics)

    if warnings:
        return Result(FAIL, f"{label}: {warnings} warnings", output, diagnostics)

    # Warnings in files ninja didn't recompile won't show; the syntax stage
    # recompiles everything
    suffix = ", up to date" if "no work to do" in output else ""

    return Result(PASS, label + suffix, output)


def build() -> Result:
    """Build the project using Ninja.

    Returns:
        A Result object representing the build result.
    """
    database = find_database()
    if database is None:
        return Result(SKIP, "no build/cp* dir; run `just install`")

    build_dir = database.parent.relative_to(ROOT)

    return compile_result(*run(tool("ninja"), "-C", build_dir), str(build_dir))


def meson_suite(
    build_dir: str, setup_args: tuple[str, ...] = (), env: dict[str, str] | None = None
) -> Result:
    """Configure (on first use), build and run the Unity suite in `build_dir`.

    Args:
        build_dir: The build directory to use.
        setup_args: Additional arguments to pass to `meson setup`.
        env: Environment variables to set for the `meson test` process.

    Returns:
        A Result object representing the meson suite result.
    """
    output = ""
    if not (ROOT / build_dir).is_dir():
        code, output = run(tool("meson"), "setup", build_dir, "tests/c", *setup_args)
        if code:
            return Result(FAIL, "meson setup failed", output, [])

    compiled = compile_result(*run(tool("ninja"), "-C", build_dir), build_dir)
    if compiled.status == FAIL:
        return compiled

    code, tests = run(
        tool("meson"), "test", "-C", build_dir, "--print-errorlogs", env=env
    )
    counts = {name: int(n) for name, n in MESON_COUNT.findall(tests)}
    detail = ", ".join(
        f"{n} {name.lower()}" for name, n in counts.items() if n or name == "Ok"
    )
    if code:
        failures = matching(tests, re.compile(r"FAIL|ERROR|runtime error"))
        return Result(FAIL, detail or "meson test failed", output + tests, failures)

    return Result(PASS, detail, output + tests)


def c_tests() -> Result:
    """Run the C tests using the meson suite.

    Returns:
        A Result object representing the C tests result.
    """
    return meson_suite("build/c-tests")


def asan() -> Result:
    """Run the C tests using the meson suite with ASAN enabled.

    Returns:
        A Result object representing the ASAN result.
    """
    return meson_suite(
        "build/c-asan",
        ("-Db_sanitize=address,undefined", "-Db_lundef=false"),
        ASAN_ENV,
    )


def syntax() -> Result:
    """Check the C syntax of all source files.

    Returns:
        A Result object representing the syntax check result.
    """
    database = find_database()
    if database is None:
        return Result(SKIP, "no build/cp* dir; run `just install`")

    # Passing every file, rather than none, also covers the C tests and their
    # support code
    paths = [
        *load_commands(database),
        *sorted(CSRC.rglob("*.h")),
        *sorted(TESTS_DIR.glob("*.c")),
        *sorted((TESTS_DIR / "support").glob("*.[ch]")),
    ]
    code, output = run(sys.executable, "scripts/check_c_syntax.py", *paths)
    diagnostics = matching(output, COMPILER_DIAGNOSTIC)
    if code or diagnostics:
        return Result(FAIL, f"{len(diagnostics)} diagnostics", output, diagnostics)

    return Result(PASS, f"{len(paths)} files", output)


def codec(record: bool) -> Result:
    """Run the codec fixtures script.

    Args:
        record: Whether to record new codec fixtures.

    Returns:
        A Result object representing the codec result.
    """
    code, output = run(
        sys.executable, "scripts/codec_fixtures.py", "record" if record else "check"
    )
    if code:
        changes = re.compile(r"^(CHANGED|ADDED|MISSING|MISMATCH)")
        return Result(FAIL, last_line(output), output, matching(output, changes))

    return Result(PASS, last_line(output), output)


def ruff_format(fix: bool) -> Result:
    """Run the Ruff formatter.

    Args:
        fix: Whether to fix formatting issues.

    Returns:
        A Result object representing the Ruff format result.
    """
    code, output = run(tool("ruff"), "format", *([] if fix else ["--check"]))
    reformat = matching(output, re.compile(r"^Would reformat"))

    return Result(FAIL if code else PASS, last_line(output), output, reformat)


def lint(fix: bool) -> Result:
    """Run the Ruff linter.

    Args:
        fix: Whether to fix linting issues.

    Returns:
        A Result object representing the lint result.
    """
    code, output = run(
        tool("ruff"), "check", "--output-format", "concise", *(["--fix"] if fix else [])
    )

    return Result(FAIL if code else PASS, last_line(output), output)


def type_check() -> Result:
    """Run the type checker.

    Returns:
        A Result object representing the type check result.
    """
    code, output = run(tool("ty"), "check", "--output-format", "concise")

    return Result(FAIL if code else PASS, last_line(output), output)


def pytest() -> Result:
    """Run the pytest test suite.

    Returns:
        A Result object representing the pytest result.
    """
    code, output = run(tool("pytest"), "-n", "auto", "-q")
    summaries = [
        m.group(1) for m in map(PYTEST_SUMMARY.match, output.splitlines()) if m
    ]
    detail = summaries[-1] if summaries else last_line(output)
    if code:
        failures = matching(output, re.compile(r"^(FAILED|ERROR) "))
        return Result(FAIL, detail, output, failures)

    return Result(PASS, detail, output)


def timed(
    stage: Callable[[], Result], after: list[Future[tuple[Result, float]]]
) -> tuple[Result, float]:
    """Run a stage once the stages it depends on have finished.

    Args:
        stage: The stage to run.
        after: The futures of the stages it depends on.

    Returns:
        The stage's result and how long it ran, not counting the wait.
    """
    # Not `result()`, so a dependency that raised doesn't take this stage too
    wait(after)
    start = time.monotonic()
    result = stage()

    return result, time.monotonic() - start


def untracked() -> set[str]:
    """Get the set of untracked files in the repository.

    Returns:
        A set of file paths that are not tracked by Git.
    """
    _, output = run("git", "status", "--porcelain", "--untracked-files=all")

    return {line[3:] for line in output.splitlines() if line.startswith("??")}


def affected_stages() -> set[str] | None:
    """Choose stages from the uncommitted changes, staged or not.

    Returns:
        The stages to run, or None to run them all, which is what a change to
        anything other than C, Python or Markdown gets, and what a clean tree
        gets.
    """
    _, diff = run("git", "diff", "--name-only", "HEAD")
    _, new = run("git", "ls-files", "--others", "--exclude-standard")
    changed = [*diff.splitlines(), *new.splitlines()]
    if not changed:
        return None

    stages: set[str] = set()
    for path in changed:
        suffix = Path(path).suffix
        if suffix in {".c", ".h"}:
            stages |= C_STAGES
        elif suffix in {".py", ".pyi"}:
            stages |= PY_STAGES
        elif suffix not in IGNORED_SUFFIXES:
            return None

    return stages


def main() -> int:
    """Run the verification script.

    Returns:
        int: 0 for success, 1 for failure
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--changed", action="store_true", help="run only affected stages"
    )
    parser.add_argument("--only", default="", help="comma-separated stages to run")
    parser.add_argument("--skip", default="", help="comma-separated stages to skip")
    parser.add_argument("--fix", action="store_true", help="let ruff rewrite files")
    parser.add_argument(
        "--record", action="store_true", help="re-record the codec manifest"
    )
    args = parser.parse_args()

    stages: dict[str, Callable[[], Result]] = {
        "build": build,
        "c-tests": c_tests,
        "asan": asan,
        "syntax": syntax,
        "codec": lambda: codec(args.record),
        "format": lambda: ruff_format(args.fix),
        "lint": lambda: lint(args.fix),
        "type": type_check,
        "pytest": pytest,
    }

    only = {s for s in args.only.split(",") if s}
    skip = {s for s in args.skip.split(",") if s}
    unknown = (only | skip) - stages.keys()
    if unknown:
        parser.error(f"unknown stages: {', '.join(sorted(unknown))}")

    selected = only or set(stages)
    if args.changed:
        affected = affected_stages()
        if affected is not None:
            selected &= affected
    selected -= skip

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    width = max(map(len, stages)) + 2
    untracked_before = untracked()
    results: dict[str, Result] = {}

    for name in stages:
        if name not in selected:
            print(f"{name:<{width}}{SKIP}  not selected", flush=True)

    dependencies = {
        name: (
            DEPENDENCIES.get(name, set())
            | (FIX_DEPENDENCIES.get(name, set()) if args.fix else set())
        )
        & selected
        for name in selected
    }
    futures: dict[str, Future[tuple[Result, float]]] = {}

    with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
        # In dependency order, so each stage's dependencies already have futures
        for name in TopologicalSorter(dependencies).static_order():
            after = [futures[dep] for dep in dependencies[name]]
            futures[name] = pool.submit(timed, stages[name], after)

        names = {future: name for name, future in futures.items()}
        for future in as_completed(names):
            name = names[future]
            result, elapsed = future.result()
            (LOG_DIR / f"{name}.log").write_text(result.output)
            results[name] = result
            detail = f"  {result.detail}" if result.detail else ""
            status = f"{result.status}{detail} ({elapsed:.1f}s)"
            print(f"{name:<{width}}{status}", flush=True)

    # Untracked files from before the run may be the change's new files, so
    # only the ones the run itself created count as stray
    untracked_after = untracked()
    stray = sorted(untracked_after - untracked_before)
    if stray:
        print(f"{'clean':<{width}}{FAIL}  the run left {len(stray)} untracked files")
    else:
        print(f"{'clean':<{width}}{PASS}  {len(untracked_after)} untracked files")
    for path in stray or sorted(untracked_after):
        print(f"  {path}")

    # In stage order, not the order the stages finished in
    failed = {
        name: results[name]
        for name in stages
        if name in results and results[name].status == FAIL
    }
    for name, result in failed.items():
        lines = result.diagnostics or result.output.splitlines()[-TAIL_LINES:]
        shown = lines[:TAIL_LINES]
        print(f"\n── {name} ({(LOG_DIR / f'{name}.log').relative_to(ROOT)})")
        print("\n".join(shown))
        if len(lines) > len(shown):
            print(f"… {len(lines) - len(shown)} more lines in the log")

    return 1 if failed or stray else 0


if __name__ == "__main__":
    sys.exit(main())

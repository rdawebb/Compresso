"""Removes caches and the built extension, plus any build/ groups named.

Usage:
    python scripts/clean.py                 # caches and the built extension
    python scripts/clean.py bench fixtures  # also the named groups
    python scripts/clean.py all             # also everything else under build/
"""

import argparse
import contextlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Installed packages have their own __pycache__ and extension modules
SKIP_DIRS = {".venv", ".git"}

CACHE_DIRS = [
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "htmlcov",
    "dist",
    "compresso.egg-info",
]
CACHE_FILES = [".coverage"]
EXTENSION_SUFFIXES = [".so", ".pyd", ".dll"]

GROUPS = {
    "c": ["build/c-tests", "build/c-asan", "build/c-fuzz"],
    "bench": ["build/bench"],
    "fixtures": ["build/codec-fixtures"],
    "logs": ["build/verify", "build/ci-logs"],
}


def walk(pattern: str) -> list[Path]:
    """Match `pattern` anywhere under the root, outside SKIP_DIRS.

    Args:
        pattern: The pattern to be matched for removal.

    Returns:
        A list of matched filepaths.
    """
    return [
        path
        for path in ROOT.rglob(pattern)
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
    ]


def remove(path: Path) -> None:
    """Remove a file or directory tree, ignoring ones already gone.

    Args:
        path: The filepath to be removed.
    """
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        # Ignore files that vanish or are locked mid-sweep
        with contextlib.suppress(OSError):
            path.unlink()


def clean(groups: list[str]) -> None:
    """Remove the caches and extension, then each named group.

    Args:
        groups: The additional target groups to be removed.
    """
    targets = [path for name in CACHE_DIRS + CACHE_FILES for path in walk(name)]
    targets += ROOT.glob("build/cp*")
    targets += [
        p for ext in EXTENSION_SUFFIXES for p in (ROOT / "src").rglob(f"*{ext}")
    ]

    if "all" in groups:
        targets.append(ROOT / "build")
    else:
        targets += [ROOT / path for name in groups for path in GROUPS[name]]

    for path in targets:
        remove(path)

    print("Cleaned:", ", ".join(["caches", "extension", *groups]))


def main() -> None:
    """Parse the group names and clean."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("groups", nargs="*", default=[], choices=[*GROUPS, "all"])
    clean(parser.parse_args().groups)


if __name__ == "__main__":
    main()

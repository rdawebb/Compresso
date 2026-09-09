"""C syntax-check script

Compiles the given C sources with `-fsyntax-only`, reusing the include dirs that
`setup.py` resolves for the real build.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# setup.py lives at the repo root and is not importable from this directory
sys.path.insert(0, str(ROOT))

import setup


def compiler() -> list[str]:
    """Return the C compiler command to invoke.

    Returns:
        The compiler from sysconfig's `CC` (what the extension is actually built
        with), falling back to `cc` when Python reports none.
    """
    cc = sysconfig.get_config_var("CC")

    return cc.split() if cc else ["cc"]


def check(paths: list[str]) -> int:
    """Syntax-check `paths`.

    Args:
        paths: C source files to check.

    Returns:
        The compiler's exit code, or 0 when there is nothing to check.
    """
    if not paths:
        return 0

    include_dirs, _ = setup.resolve_dirs()
    include_dirs.append(sysconfig.get_paths()["include"])

    cmd = [
        *compiler(),
        *(f"-I{d}" for d in include_dirs),
        "-std=gnu11",
        "-fsyntax-only",
        *paths,
    ]

    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    args = sys.argv[1:]
    # With no arguments, check every source the extension builds
    files = args or [str(ROOT / src) for src in setup.C_SOURCES]
    sys.exit(check(files))

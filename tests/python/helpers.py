"""Helpers shared across the Python test modules."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from collections.abc import Callable, Mapping
from pathlib import Path

ARCHIVE_FORMATS = [("tar", ".tar"), ("tar.zst", ".tar.zst"), ("zip", ".zip")]

# The numbered-sibling suffix `RENAME` inserts: "name 2.ext" on macOS, "name
# (2).ext" elsewhere
_CONFLICT_SUFFIX = " {}" if sys.platform == "darwin" else " ({})"


def renamed(name: str, n: int = 2) -> str:
    """The name the `RENAME` overwrite mode gives the `n`th clash with `name`.

    The suffix goes before the first dot, so a compound extension such as
    ".tar.zst" stays intact.

    Args:
        name: The clashing file or directory name.
        n: Which numbered sibling to name.

    Returns:
        The renamed sibling's name.
    """
    stem, dot, extension = name.partition(".")

    return f"{stem}{_CONFLICT_SUFFIX.format(n)}{dot}{extension}"


class Recorder:
    """Collects progress callbacks for assertions."""

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls: list[tuple[int, int]] = []

    def __call__(self, done: int, total: int) -> None:
        """Record one progress report.

        Args:
            done: The number of bytes processed so far.
            total: The total number of bytes to process.
        """
        self.calls.append((done, total))

    @property
    def dones(self) -> list[int]:
        """The done count of each report, in order.

        Returns:
            A list of done counts, one per report.
        """
        return [done for done, _ in self.calls]

    def assert_monotonic(self) -> None:
        """Assert that progress never went backwards."""
        assert self.dones == sorted(self.dones), "progress went backwards"

    def assert_finished(self) -> None:
        """Assert that the last report was at 100%."""
        assert self.calls, "no progress was reported"
        done, total = self.calls[-1]
        assert done == total, f"final report was {done}/{total}, not 100%"


def make_tar(
    path: Path,
    *infos: tarfile.TarInfo,
    fill: Callable[[int], bytes] = lambda n: b"x" * n,
) -> bytes:
    """Write a GNU tar of `infos`, stored exactly as given.

    Goes through `tarfile` rather than the archiver, so a test can store
    names, types and links the archiver itself would never produce.

    Args:
        path: Where to write the tar.
        infos: Entry headers, in archive order.
        fill: Makes a regular file's `size` bytes of data.

    Returns:
        The tar's bytes.
    """
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as tf:
        for info in infos:
            data = io.BytesIO(fill(info.size)) if info.isreg() else None
            tf.addfile(info, data)

    return path.read_bytes()


def file_entry(
    name: str, size: int = 1, mode: int = 0o644, mtime: int = 0
) -> tarfile.TarInfo:
    """A regular file's tar header.

    Args:
        name: The entry name.
        size: The data size `make_tar` fills.
        mode: The stored permission bits.
        mtime: The stored mtime.

    Returns:
        The tar header.
    """
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, mode, mtime

    return info


def dir_entry(name: str, mode: int = 0o755, mtime: int = 0) -> tarfile.TarInfo:
    """A directory's tar header.

    Args:
        name: The entry name.
        mode: The stored permission bits.
        mtime: The stored mtime.

    Returns:
        The tar header.
    """
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.mtime = tarfile.DIRTYPE, mode, mtime

    return info


def link_entry(
    name: str, target: str, kind: bytes = tarfile.LNKTYPE, mtime: int = 0
) -> tarfile.TarInfo:
    """A hardlink's tar header, or a symlink's with `kind=tarfile.SYMTYPE`.

    Args:
        name: The entry name.
        target: The path the link names.
        kind: The link's tar type.
        mtime: The stored mtime.

    Returns:
        The tar header, with mode 0o777 as tar stores a symlink's.
    """
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.mode, info.mtime = kind, target, 0o777, mtime

    return info


def node_entry(
    name: str, kind: bytes, mode: int = 0o644, mtime: int = 0
) -> tarfile.TarInfo:
    """A FIFO or device node's tar header, with device number 1:3.

    Args:
        name: The entry name.
        kind: The node's tar type.
        mode: The stored permission bits.
        mtime: The stored mtime.

    Returns:
        The tar header.
    """
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.mtime = kind, mode, mtime
    info.devmajor, info.devminor = 1, 3

    return info


def umask() -> int:
    """Read the process umask without leaving it changed.

    Returns:
        The current umask.
    """
    current = os.umask(0)
    os.umask(current)

    return current


def run_child(
    script: str, *args: object, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `script` in a child Python, for what would hang or crash the worker.

    Args:
        script: The source to run with `python -c`.
        args: The script's arguments, converted with `str`.
        env: The child's environment, the parent's unless given.

    Returns:
        The finished process, its output decoded as UTF-8.
    """
    return subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )

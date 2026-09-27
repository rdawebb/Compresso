"""Helpers shared across the Python test modules."""

from __future__ import annotations

import sys

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

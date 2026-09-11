"""Shared job-lifecycle primitives for the frontend APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Progress callback signature: (fraction, done_bytes, total_bytes).
ProgressCallback = Callable[[float, int, int], None]


@dataclass
class JobResult:
    """Holds the result of a compression, decompression, or archive job.

    Attributes:
        ok: Indicates if the job was successful.
        error: The error encountered, if any.
        plan: The associated plan (a compression, decompression, archive, or
            extraction plan).
        cancelled: True if the job stopped because its cancel token was set.
    """

    ok: bool
    error: BaseException | None
    plan: object
    cancelled: bool = False


def to_core_progress(
    progress: ProgressCallback | None, total: int
) -> Callable[[int, int], None] | None:
    """Adapt a :data:`ProgressCallback` to the two-argument form `_core` calls.

    Args:
        progress: The progress callback function.
        total: The total size of the input, used as a fallback when the C layer
            does not report progress.

    Returns:
        A function that can be used as a progress callback for `_core` calls,
        or None if no callback was provided.
    """
    if progress is None:
        return None

    def on_progress(done: int, core_total: int) -> None:
        effective: int = core_total or total
        fraction: float = (done / effective) if effective else 0.0
        progress(fraction, done, effective)

    return on_progress


@runtime_checkable
class Job(Protocol):
    """Structural contract shared by every frontend job.

    A job exposes the ``plan`` it will execute and a ``run`` method that
    performs the work and returns a :class:`JobResult` (never raising).
    """

    plan: object

    def run(self, progress: ProgressCallback | None = None) -> JobResult:
        """Execute the job and return its result."""
        ...

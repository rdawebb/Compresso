"""Shared job-lifecycle primitives for the frontend APIs.

Both :mod:`compresso.frontend.api` (single-file compress/decompress) and
:mod:`compresso.frontend.archive_api` (multi-file archive/extract) build on the
same contract: a *plan* describes the work and whether it can proceed, a *job*
executes it, and a :class:`JobResult` reports the outcome without ever raising.
These primitives live here so the two APIs share one definition instead of
drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

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
    """

    ok: bool
    error: BaseException | None
    plan: object


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

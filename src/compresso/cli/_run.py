"""Running a command's jobs one input at a time, carrying on past a failure."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from .._core import Error
from ..frontend._job import ThreadedJob
from ._render import (
    BAR_THRESHOLD,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    cancelled,
    fail,
    progress_bar,
    report_error,
)

J = TypeVar("J")


class InputFailed(Exception):
    """One input failed; the runner reports it and goes on to the next."""


def require_files(paths: Iterable[Path]) -> None:
    """Exit 2 before any input runs if one is missing or not a file.

    A stat shows these; what only an input's bytes show fails that input alone.

    Args:
        paths: The inputs, as given.
    """
    for path in paths:
        if not path.is_file():
            fail(f"Error: {path} does not exist or is not a file", EXIT_USAGE)


def run_job(
    job: ThreadedJob[Any],
    source: Path,
    label: str,
    total: int,
    *,
    quiet: bool,
    what: str,
) -> float:
    """Run `job` under a progress bar.

    Args:
        job: The planned job, already checked as runnable.
        source: The input, named in the error if the job fails.
        label: Text shown beside the bar.
        total: Expected byte count; small jobs get no bar.
        quiet: Whether to suppress the bar.
        what: Operation name for messages, e.g. "Compression".

    Returns:
        Seconds the job took.

    Raises:
        InputFailed: If the job failed.
    """
    start_time: float = time.time()

    with progress_bar(
        label, total, enabled=not quiet and total > BAR_THRESHOLD
    ) as on_progress:
        result = job.run(progress=on_progress)

    elapsed: float = time.time() - start_time

    if result.cancelled:
        cancelled(what)

    if not result.ok:
        raise InputFailed(f"{source}: {what} failed: {result.error}")

    return elapsed


def run_all(jobs: Iterable[J], run_one: Callable[[J], None], what: str) -> int:
    """Run each job in turn, reporting a failed one and going on to the next.

    Args:
        jobs: The planned jobs, one per input.
        run_one: Runs one job, raising `InputFailed`, `Error` or `OSError` if
            it fails; anything else is a bug and stops the run.
        what: Operation name for messages, e.g. "Compression".

    Returns:
        EXIT_FAILED if any job failed, else EXIT_OK.
    """
    failed = False
    for job in jobs:
        try:
            run_one(job)

        except InputFailed as e:
            report_error(str(e))
            failed = True

        except Error as e:
            report_error(f"{what} error: {e}")
            failed = True

        # As gzip does, e.g. an input removed or made unreadable since the stat
        except OSError as e:
            report_error(f"{e.filename}: {e.strerror}" if e.filename else str(e))
            failed = True

    return EXIT_FAILED if failed else EXIT_OK

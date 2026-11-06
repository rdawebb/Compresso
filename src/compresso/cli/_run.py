"""Running a command's jobs one input at a time, carrying on past a failure."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from .._core import Error, SourceRemovalError
from ..frontend._job import JobResult, ThreadedJob
from ..frontend.archive_api import OverwriteMode
from ._render import (
    BAR_THRESHOLD,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    cancelled,
    fail,
    progress_bar,
    report_error,
    report_warning,
)

J = TypeVar("J")


class InputFailed(Exception):
    """One input failed; the runner reports it and goes on to the next."""


class NotRemoved(Exception):
    """An output is written, but `--rm` left inputs; args are (path, reason)."""


def require_files(paths: Iterable[Path]) -> None:
    """Exit 2 before any input runs if one is missing or not a file.

    A stat shows these; what only an input's bytes show fails that input alone.

    Args:
        paths: The inputs, as given.
    """
    for path in paths:
        if not path.is_file():
            fail(f"Error: {path} does not exist or is not a file", EXIT_USAGE)


def conflict_mode(
    on_conflict: OverwriteMode | None, force: bool, remove: bool
) -> OverwriteMode:
    """Resolve `--on-conflict` and `-f` into one mode, exiting 2 if they disagree.

    Args:
        on_conflict: The mode given, or None if it was not.
        force: Whether `-f`, shorthand for `--on-conflict overwrite`, was given.
        remove: Whether `--rm`, which can't follow an output that was skipped,
            was given.

    Returns:
        The mode to run with; RENAME if neither was given.
    """
    if not force:
        if remove and on_conflict is OverwriteMode.SKIP:
            fail(
                "Error: --rm contradicts --on-conflict skip; "
                "use --on-conflict error to keep inputs whose output exists",
                EXIT_USAGE,
            )

        return on_conflict or OverwriteMode.RENAME

    if on_conflict not in (None, OverwriteMode.OVERWRITE):
        fail(f"Error: -f contradicts --on-conflict {on_conflict}", EXIT_USAGE)

    return OverwriteMode.OVERWRITE


def remove_source(source: Path, before: os.stat_result) -> None:
    """Delete an input now its output is written when '--rm' flag is used.

    Args:
        source: The input.
        before: Its stat from before the job; anything else at that path now
            is the output, written over it, so it stays.

    Raises:
        NotRemoved: If the input can't be deleted.
    """
    try:
        if os.path.samestat(source.stat(), before):
            source.unlink()

    except OSError as e:
        raise NotRemoved((str(source), e.strerror or str(e))) from e


def run_job(
    job: ThreadedJob[Any],
    source: Path,
    label: str,
    total: int,
    *,
    quiet: bool,
    what: str,
    verbose: bool = False,
) -> tuple[float, JobResult]:
    """Run `job` under a progress bar.

    Args:
        job: The planned job, already checked as runnable.
        source: The input, named in the error if the job fails.
        label: Text shown beside the bar.
        total: Expected byte count; small jobs get no bar.
        quiet: Whether to suppress the bar.
        what: Operation name for messages, e.g. "Compression".
        verbose: Whether a blank line follows the bar, to set it apart from
            the stats block.

    Returns:
        Seconds the job took, and its result; a `SourceRemovalError` in that
        result is left for the caller, as the output is still there to report.

    Raises:
        InputFailed: If the job failed.
    """
    start_time: float = time.time()

    bar: bool = not quiet and total > BAR_THRESHOLD
    with progress_bar(label, total, enabled=bar) as on_progress:
        result = job.run(progress=on_progress)

    if bar and verbose:
        print()

    elapsed: float = time.time() - start_time

    if result.cancelled:
        cancelled(what)

    if not result.ok and not isinstance(result.error, SourceRemovalError):
        raise InputFailed(f"{source}: {what} failed: {result.error}")

    return elapsed, result


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

        except NotRemoved as e:
            for path, reason in e.args:
                report_warning(f"{path}: not removed: {reason}")
            failed = True

        except Error as e:
            report_error(f"{what} error: {e}")
            failed = True

        # As gzip does, e.g. an input removed or made unreadable since the stat
        except OSError as e:
            report_error(f"{e.filename}: {e.strerror}" if e.filename else str(e))
            failed = True

    return EXIT_FAILED if failed else EXIT_OK

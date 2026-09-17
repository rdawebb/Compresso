"""Shared rendering for the CLI: formatting, progress and exit codes."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from ..frontend._job import JobResult, ProgressCallback
from ._app import app

# Exit codes
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

# Below this, a job finishes faster than bar rendering
BAR_THRESHOLD = 1024 * 1024


def format_size(size_bytes: float) -> str:
    """Format byte size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"

        size_bytes: float = size_bytes / 1024.0

    return f"{size_bytes:.2f} PB"


def format_time(seconds: float) -> str:
    """Format time duration in human-readable format.

    Args:
        seconds: Time in seconds

    Returns:
        Formatted string (e.g., "1.5s", "2m 30s")
    """
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"

    elif seconds < 60:
        return f"{seconds:.2f}s"

    else:
        mins: int = int(seconds // 60)
        secs: float = seconds % 60

        return f"{mins}m {secs:.1f}s"


def fail(message: str, code: int = EXIT_FAILED) -> NoReturn:
    """Print an error to stderr and exit.

    Args:
        message: Text to show, without the leading marker.
        code: Process exit code; see the EXIT_* constants.
    """
    app.echo(message=app.style(text=f"✗ {message}", fg="red"), err=True)
    sys.exit(code)


def succeed(message: str) -> None:
    """Print a success line.

    Args:
        message: Text to show, without the leading marker.
    """
    app.echo(message=app.style(text=f"✓ {message}", fg="green"))


def cancelled(what: str) -> NoReturn:
    """Report an interrupted operation and exit with the SIGINT code.

    Args:
        what: Operation name for the message, e.g. "Compression".
    """
    app.echo(message=app.style(text=f"\n✗ {what} cancelled", fg="yellow"), err=True)
    sys.exit(EXIT_CANCELLED)


def exit_for_result(result: JobResult, what: str) -> None:
    """Exit with the right code if `result` did not succeed.

    A cancelled job is not a failure, so it gets the conventional SIGINT
    code rather than the generic failure one.

    Args:
        result: The outcome of a frontend job.
        what: Operation name for the message, e.g. "Compression".
    """
    if result.cancelled:
        cancelled(what)

    if not result.ok:
        fail(f"\n{what} failed: {result.error}")


@contextmanager
def progress_bar(
    label: str, total: int, *, enabled: bool
) -> Iterator[ProgressCallback | None]:
    """Yield a progress callback that draws a bar, or None.

    Args:
        label: Text shown beside the bar.
        total: Expected byte count, used until the job reports its own.
        enabled: False yields None, which runs the job without a bar.

    Yields:
        A callback to hand to a job's `run`, or None.
    """
    if not enabled:
        yield None
        return

    # length must be positive to render a bar at all
    with app.progressbar(
        length=max(total, 1),
        label=label,
        show_eta=True,
        show_percent=True,
    ) as bar:

        def on_progress(fraction: float, done: int, reported_total: int) -> None:
            # A two-stage job (e.g. tar.zst) takes the denominator from the job
            if reported_total and reported_total != bar.length:
                bar.length = reported_total

            # Byte counts are absolute and never decrease
            bar.update(n_steps=done - bar.pos)

        yield on_progress

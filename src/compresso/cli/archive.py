"""The `archive` command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.archive_api import ArchiveJob, ArchiveOptions
from ._app import app
from ._render import (
    BAR_THRESHOLD,
    EXIT_USAGE,
    cancelled,
    exit_for_result,
    fail,
    format_size,
    format_time,
    progress_bar,
    succeed,
)


def archive(
    output: Annotated[Path, app.Argument(help="Output archive path")],
    sources: Annotated[
        list[str], app.Argument(help="Files and directories to archive")
    ],
    format: Annotated[
        str,
        app.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Archive format (e.g. tar.zst, tar.gz, tar)",
        ),
    ] = "tar.zst",
    level: Annotated[
        int | None,
        app.Option("--level", "-l", min=0, max=9, help="Compression level (0-9)"),
    ] = None,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Create an archive from multiple files and directories.

    Args:
        output: The path to the output archive file.
        sources: The files and directories to include in the archive.
        format: The archive format (default: "tar.zst").
        level: The compression level to use (default: None).
        quiet: If True, suppress all output (default: False).
    """
    try:
        options = ArchiveOptions(format=format.lower(), compression_level=level)
        job = ArchiveJob.from_paths(sources=sources, output=output, options=options)
        plan = job.plan

        if not plan.can_run:
            fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

        if not quiet:
            app.echo(message=f"Archiving:   {plan.entry_count} source(s)")
            app.echo(message=f"Output:      {plan.output}")
            app.echo(message=f"Format:      {plan.options.format}")
            app.echo(
                message=f"Input size:  {format_size(size_bytes=plan.total_input_size)}"
            )
            app.echo()

        start_time: float = time.time()

        # A format with a codec stage reads the data twice, so the job reports
        # a larger total than this once it starts
        with progress_bar(
            "Archiving",
            plan.total_input_size,
            enabled=not quiet and plan.total_input_size > BAR_THRESHOLD,
        ) as on_progress:
            result = job.run(progress=on_progress)

        elapsed: float = time.time() - start_time

        exit_for_result(result, "Archive")

        archive_size: int = plan.output.stat().st_size

        if not quiet:
            succeed("Archive created!")
            app.echo(
                message=f"  Input size:   {format_size(size_bytes=plan.total_input_size)}"
            )
            app.echo(message=f"  Archive size: {format_size(size_bytes=archive_size)}")
            app.echo(message=f"  Time:         {format_time(seconds=elapsed)}")
            app.echo()

    except KeyboardInterrupt:
        cancelled("Archive")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Archive error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["a", "ar"])(archive)

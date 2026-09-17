"""The `extract` command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.archive_api import ExtractJob, ExtractOptions, OverwriteMode
from ._app import app
from ._render import (
    BAR_THRESHOLD,
    EXIT_USAGE,
    cancelled,
    exit_for_result,
    fail,
    format_time,
    progress_bar,
    succeed,
)


def extract(
    archive: Annotated[Path, app.Argument(help="Archive to extract")],
    output_dir: Annotated[
        Path | None,
        app.Option(
            "--output-dir",
            "-o",
            help="Directory to extract into (default: current directory)",
        ),
    ] = None,
    list_only: Annotated[
        bool, app.Option("--list", help="List archive contents without extracting")
    ] = False,
    overwrite: Annotated[
        bool, app.Option("--overwrite", help="Replace files that already exist")
    ] = False,
    skip_existing: Annotated[
        bool, app.Option("--skip-existing", help="Leave files that already exist")
    ] = False,
    max_total_size: Annotated[
        int | None,
        app.Option(
            "--max-total-size",
            help="Refuse archives extracting to more than this many bytes",
        ),
    ] = None,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Extract an archive, or list its contents.

    Extraction refuses to touch an existing file unless either `--overwrite`
    or `--skip-existing` flags are explicitly used.

    Args:
        archive: The path to the archive file.
        output_dir: Directory to extract into (default: current directory).
        list_only: If True, list contents without extracting.
        overwrite: If True, replace files that already exist.
        skip_existing: If True, leave files that already exist untouched.
        max_total_size: Cap on total extracted bytes (default: no cap).
        quiet: If True, suppress all output (default: False).
    """
    if overwrite and skip_existing:
        fail(
            "Error: --overwrite and --skip-existing are mutually exclusive",
            EXIT_USAGE,
        )

    if overwrite:
        mode = OverwriteMode.OVERWRITE
    elif skip_existing:
        mode = OverwriteMode.SKIP
    else:
        mode = OverwriteMode.ERROR

    options = ExtractOptions(
        overwrite=mode,
        max_total_size=max_total_size or 0,
    )

    try:
        job = ExtractJob.from_archive(
            archive=archive, output_dir=output_dir, options=options
        )
        plan = job.plan

        if not plan.can_run:
            fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

        if list_only:
            for entry in job.list_contents():
                app.echo(message=entry.path)
            return

        if not quiet:
            app.echo(message=f"Extracting: {plan.archive}")
            app.echo(message=f"Output dir: {plan.output_dir}")
            app.echo(message=f"Entries:    {len(plan.entries)}")
            app.echo()

        start_time: float = time.time()

        # Progress covers the bytes written out; a compressed archive also
        # reports the pass that decompresses it, so the job's own total wins
        extracted_size: int = sum(entry.size for entry in plan.entries)

        with progress_bar(
            "Extracting",
            extracted_size,
            enabled=not quiet and extracted_size > BAR_THRESHOLD,
        ) as on_progress:
            result = job.run(progress=on_progress)

        elapsed: float = time.time() - start_time

        exit_for_result(result, "Extraction")

        if not quiet:
            succeed("Extraction successful!")
            app.echo(message=f"  Entries: {len(plan.entries)}")
            app.echo(message=f"  Time:    {format_time(seconds=elapsed)}")
            app.echo()

    except KeyboardInterrupt:
        cancelled("Extraction")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Extraction error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["x", "ex"])(extract)

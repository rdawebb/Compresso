"""The `decompress` command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import DecompressionJob
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


def decompress(
    file: Annotated[Path, app.Argument(help="File to decompress")],
    output: Annotated[
        Path | None,
        app.Option(
            "--output",
            "-o",
            help="Output file path (default: remove .comp extension)",
        ),
    ] = None,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress progress output")
    ] = False,
) -> None:
    """Decompress a Compresso compressed file.

    Args:
        file: The path to the compressed file.
        output: The path to the output file (default: remove .comp extension).
        quiet: If True, suppress progress output.
    """
    try:
        job = DecompressionJob.from_file(src=file, dest=output)
        plan = job.plan
        insp = plan.inspection

        if not insp.is_compresso:
            fail(
                f"Error: Not a valid Compresso file\n  Reason: {insp.reason}",
                EXIT_USAGE,
            )

        if not insp.header_ok:
            fail(f"Error: Invalid file header\n  Reason: {insp.reason}", EXIT_USAGE)

        if not insp.can_decompress:
            fail(f"Error: Cannot decompress file\n  Reason: {insp.reason}", EXIT_USAGE)

        if not quiet:
            app.echo(message=f"Decompressing: {plan.src}")
            app.echo(message=f"Output:        {plan.dest}")
            app.echo(message=f"Algorithm:     {insp.algo_name}")
            if insp.orig_size:
                app.echo(
                    message=f"Original size: {format_size(size_bytes=insp.orig_size)}"
                )
            app.echo()

        start_time: float = time.time()

        # Progress counts the compressed bytes read, so the source's size is
        # the denominator rather than the size it expands to
        compressed_size: int = plan.src.stat().st_size

        with progress_bar(
            "Decompressing",
            compressed_size,
            enabled=not quiet and compressed_size > BAR_THRESHOLD,
        ) as on_progress:
            result = job.run(progress=on_progress)

        elapsed: float = time.time() - start_time

        exit_for_result(result, "Decompression")

        decompressed_size: int = plan.dest.stat().st_size
        speed_mbs: int | float = (
            (decompressed_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        )

        if not quiet:
            succeed("Decompression successful!")
            app.echo()
            app.echo(
                message=f"  Compressed size:   {format_size(size_bytes=compressed_size)}"
            )
            app.echo(
                message=f"  Decompressed size: {format_size(size_bytes=decompressed_size)}"
            )
            app.echo(message=f"  Time:              {format_time(seconds=elapsed)}")
            app.echo(message=f"  Speed:             {speed_mbs:.2f} MB/s")
            app.echo()

    except KeyboardInterrupt:
        cancelled("Decompression")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Decompression error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["d", "decomp"])(decompress)

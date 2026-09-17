"""The `compress` command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import CompressionJob, CompressionOptions
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


def compress(
    file: Annotated[Path, app.Argument(help="File to compress")],
    output: Annotated[
        Path | None,
        app.Option("--output", "-o", help="Output file path (default: input.comp)"),
    ] = None,
    algo: Annotated[
        str | None,
        app.Option(
            "--algo",
            "-a",
            case_sensitive=False,
            help="Compression algorithm to use",
        ),
    ] = "auto",
    strategy: Annotated[
        str,
        app.Option(
            "--strategy",
            "-s",
            case_sensitive=False,
            help="Compression strategy to use (fast/balanced/max_ratio)",
        ),
    ] = "balanced",
    level: Annotated[
        int | None,
        app.Option("--level", "-l", min=0, max=9, help="Compression level (0-9)"),
    ] = None,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Compress a file using the specified algorithm and strategy.

    Args:
        file: The path to the file to compress.
        output: The path to the output file (default: None).
        algo: The compression algorithm to use (default: None).
        strategy: The compression strategy to use (default: "balanced").
        level: The compression level to use (default: None).
        quiet: If True, suppress all output (default: False).
    """
    try:
        algo_lower: str | None = algo.lower() if algo else None
        options = CompressionOptions(
            algo=None if algo_lower == "auto" else algo_lower,
            strategy=strategy.lower(),
            level=level,
        )

        job = CompressionJob.from_file(src=file, dest=output, options=options)
        plan = job.plan

        if not plan.can_compress:
            fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

        if not quiet:
            app.echo(message=f"Compressing: {plan.src}")
            app.echo(message=f"Output:      {plan.dest}")
            app.echo(message=f"Algorithm:   {plan.backend_name}")
            app.echo(message=f"Strategy:    {strategy}")
            if level is not None:
                app.echo(message=f"Level:       {level}")
            app.echo()

        start_time: float = time.time()

        with progress_bar(
            "Compressing",
            plan.input_size,
            enabled=not quiet and plan.input_size > BAR_THRESHOLD,
        ) as on_progress:
            result = job.run(progress=on_progress)

        elapsed: float = time.time() - start_time

        exit_for_result(result, "Compression")

        compressed_size: int = plan.dest.stat().st_size
        ratio: int | float = (
            (compressed_size / plan.input_size) * 100 if plan.input_size > 0 else 0
        )
        speed_mbs: int | float = (
            (plan.input_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        )

        if not quiet:
            succeed("Compression successful!")
            app.echo()
            app.echo(
                message=f"  Original size:   {format_size(size_bytes=plan.input_size)}"
            )
            app.echo(
                message=f"  Compressed size: {format_size(size_bytes=compressed_size)}"
            )
            app.echo(message=f"  Ratio:           {ratio:.1f}% of original")
            app.echo(message=f"  Time:            {format_time(seconds=elapsed)}")
            app.echo(message=f"  Speed:           {speed_mbs:.2f} MB/s")
            app.echo(
                message=f"  Saved:           {format_size(size_bytes=plan.input_size - compressed_size)}"
            )
            app.echo()

    except KeyboardInterrupt:
        cancelled("Compression")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Compression error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["c", "comp"])(compress)

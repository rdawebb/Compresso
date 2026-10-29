"""The `compress` command, packing files into archives and compressed files."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import CompressionJob
from ..frontend.archive_api import ArchiveJob, OverwriteMode
from ..frontend.dispatch import CompressRequest, plan_compress
from ._app import app
from ._render import EXIT_USAGE, cancelled, fail
from ._render.jobs import (
    archive_plan,
    archive_result,
    compress_plan,
    compress_result,
    skipped,
    summary,
)
from ._run import conflict_mode, run_all, run_job


def _compress_one_file(job: CompressionJob, quiet: bool, verbose: bool) -> None:
    """Compress a single file into a one-file container.

    Args:
        job: The planned job.
        quiet: Whether to suppress all output.
        verbose: Whether to print the plan and stats blocks.
    """
    plan = job.plan

    if not plan.can_compress:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if verbose:
        compress_plan(plan)

    skips: bool = plan.options.overwrite is OverwriteMode.SKIP and plan.dest.exists()

    elapsed: float = run_job(
        job, plan.src, "Compressing", plan.input_size, quiet=quiet, what="Compression"
    )

    # RENAME may have written somewhere other than `plan.dest`; job.plan is
    # replaced on success with the path actually written
    written: Path = job.plan.dest
    if verbose:
        compress_result(plan, written, elapsed)

    elif quiet:
        return

    elif skips:
        skipped(plan.dest)

    else:
        renamed: Path | None = written if written != plan.dest else None
        summary(plan.dest, plan.input_size, written.stat().st_size, elapsed, renamed)


def _archive_many(job: ArchiveJob, quiet: bool, verbose: bool) -> None:
    """Pack several paths, or a directory, into one archive.

    Args:
        job: The planned job.
        quiet: Whether to suppress all output.
        verbose: Whether to print the plan and stats blocks.
    """
    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if verbose:
        archive_plan(plan)

    skips: bool = plan.options.overwrite is OverwriteMode.SKIP and plan.output.exists()

    # A format with a codec stage reads the data twice, so the job reports a
    # larger total than this once it starts; the bar follows it
    elapsed: float = run_job(
        job,
        plan.output,
        "Archiving",
        plan.total_input_size,
        quiet=quiet,
        what="Archive",
    )

    # RENAME may have written somewhere other than `plan.output`; job.plan is
    # replaced on success with the path actually written
    written: Path = job.plan.output
    if verbose:
        archive_result(plan, written, elapsed)

    elif quiet:
        return

    elif skips:
        skipped(plan.output)

    else:
        renamed: Path | None = written if written != plan.output else None
        summary(
            plan.output,
            plan.total_input_size,
            written.stat().st_size,
            elapsed,
            renamed,
        )


def compress(
    inputs: Annotated[
        list[Path], app.Argument(help="Files and directories to compress")
    ],
    output: Annotated[
        Path | None,
        app.Option(
            "--output", "-o", help="Output path (default: named after the input)"
        ),
    ] = None,
    format: Annotated[
        str | None,
        app.Option(
            "--format",
            "-F",
            case_sensitive=False,
            help="Container to write, e.g. gz, zst, tar.zst, zip",
        ),
    ] = None,
    algo: Annotated[
        str | None,
        app.Option(
            "--algo",
            "-a",
            case_sensitive=False,
            help="Compression algorithm to use",
            show_default=False,
        ),
    ] = "auto",
    strategy: Annotated[
        str | None,
        app.Option(
            "--strategy",
            "-s",
            case_sensitive=False,
            help="Compression strategy to use (fast/balanced/max_ratio)",
            show_default="balanced",
        ),
    ] = None,
    level: Annotated[
        int | None,
        app.Option(
            "--level",
            "-l",
            help="Compression level; each algorithm's range is shown by `list`",
        ),
    ] = None,
    on_conflict: Annotated[
        OverwriteMode | None,
        app.Option(
            "--on-conflict",
            case_sensitive=False,
            help="What to do when the output exists",
            show_default="rename",
        ),
    ] = None,
    force: Annotated[
        bool,
        app.Option("--force", "-f", help="Replace an existing output"),
    ] = False,
    threads: Annotated[
        int,
        app.Option(
            "--threads",
            "-T",
            min=0,
            help="Worker threads for zstd and xz, or 0 for one per core",
        ),
    ] = 0,
    verbose: Annotated[
        bool,
        app.Option("--verbose", "-v", help="Show the plan and full stats"),
    ] = False,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Compress files or directories.
    \f

    One file becomes a single-file container: a Compresso `.comp` by default,
    or `-F gz`/`zst`/`xz`/`bz2`/`lz4` for a standalone one; a directory, or
    more than one input, becomes an archive (`zip` unless told otherwise);
    a recognisable extension on `-o` picks the format on its own, and `-F`
    overrides it.

    A clashing output name by default gets a platform-native numbered sibling
    (`name 2.ext` on macOS, `name (2).ext` elsewhere), for an archive and a
    single-file container alike; use `--on-conflict` or `-f` to change that.

    Each output gets one summary line; `-v` shows the plan and full stats
    instead, and `-q` nothing at all.

    Args:
        inputs: The files and directories to compress.
        output: Where to write (default: named after the first input).
        format: Container to write (default: inferred, else .comp or zip).
        algo: Algorithm for the Compresso container (default: auto); any
            other format refuses it.
        strategy: Strategy for the Compresso container (default: balanced);
            any other format refuses it, as does `-a`.
        level: Compression level (default: the format's own).
        on_conflict: What to do when the output exists (default: rename).
        force: If True, replace an existing output.
        threads: Worker threads for zstd and xz (default: one per core).
        verbose: If True, show the plan and full stats.
        quiet: If True, suppress all output.
    """
    overwrite_mode = conflict_mode(on_conflict, force)
    if verbose and quiet:
        fail("Error: -v contradicts -q", EXIT_USAGE)

    try:
        if not inputs:
            fail("Error: No input paths given", EXIT_USAGE)

        algo_lower: str | None = algo.lower() if algo else None
        if algo_lower == "auto":
            algo_lower = None

        # -a names the algorithm that -s would otherwise pick
        if algo_lower and strategy:
            fail("Error: -a contradicts -s", EXIT_USAGE)

        request = CompressRequest(
            inputs=inputs,
            output=output,
            format=format,
            algo=algo_lower,
            strategy=strategy.lower() if strategy else None,
            level=level,
            overwrite=overwrite_mode,
            threads=threads,
        )

        try:
            job = plan_compress(request)

        except ValueError as e:
            fail(f"Error: {e}", EXIT_USAGE)

        def run_one(job: CompressionJob | ArchiveJob) -> None:
            if isinstance(job, ArchiveJob):
                _archive_many(job, quiet, verbose)
            else:
                _compress_one_file(job, quiet, verbose)

        if not (quiet or verbose):
            print()

        sys.exit(run_all([job], run_one, "Compression"))

    except KeyboardInterrupt:
        cancelled("Compression")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Compression error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["c"])(compress)

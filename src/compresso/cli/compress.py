"""The `compress` command, which also answers to `archive`."""

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
from ._render.jobs import archive_plan, archive_result, compress_plan, compress_result
from ._run import run_all, run_job


def _compress_one_file(job: CompressionJob, quiet: bool) -> None:
    """Compress a single file into a one-file container.

    Args:
        job: The planned job.
        quiet: Whether to suppress progress output.
    """
    plan = job.plan

    if not plan.can_compress:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if not quiet:
        compress_plan(plan)

    elapsed: float = run_job(
        job, plan.src, "Compressing", plan.input_size, quiet=quiet, what="Compression"
    )

    # RENAME may have written somewhere other than `plan.dest`; job.plan is
    # replaced on success with the path actually written
    if not quiet:
        compress_result(plan, job.plan.dest, elapsed)


def _archive_many(job: ArchiveJob, quiet: bool) -> None:
    """Pack several paths, or a directory, into one archive.

    Args:
        job: The planned job.
        quiet: Whether to suppress progress output.
    """
    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if not quiet:
        archive_plan(plan)

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
    if not quiet:
        archive_result(plan, job.plan.output, elapsed)


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
            "-f",
            case_sensitive=False,
            help="Container to write, e.g. gz, zst, tar.zst, zip",
        ),
    ] = None,
    algo: Annotated[
        str | None,
        app.Option(
            "--algo", "-a", case_sensitive=False, help="Compression algorithm to use"
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
        app.Option(
            "--level",
            "-l",
            help="Compression level; each algorithm's range is shown by `list`",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        app.Option(
            "--overwrite", help="Replace an archive that already exists at the output"
        ),
    ] = False,
    skip_existing: Annotated[
        bool,
        app.Option(
            "--skip-existing", help="Leave an existing output archive untouched"
        ),
    ] = False,
    error_on_conflict: Annotated[
        bool,
        app.Option(
            "--error-on-conflict",
            help="Fail instead of renaming a clashing output archive",
        ),
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
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Compress files or directories.
    \f

    One file becomes a single-file container: a Compresso `.comp` by default,
    or `-f gz`/`zst`/`xz`/`bz2`/`lz4` for a standalone one; a directory, or
    more than one input, becomes an archive (`zip` unless told otherwise);
    a recognisable extension on `-o` picks the format on its own, and `-f`
    overrides it.

    A clashing output name by default gets a platform-native numbered sibling
    (`name 2.ext` on macOS, `name (2).ext` elsewhere), for an archive and a
    single-file container alike; use `--overwrite`, `--skip-existing`, or
    `--error-on-conflict` to change that.

    Args:
        inputs: The files and directories to compress.
        output: Where to write (default: named after the first input).
        format: Container to write (default: inferred, else .comp or zip).
        algo: Algorithm for the Compresso container (default: auto).
        strategy: Strategy for the Compresso container (default: balanced).
        level: Compression level (default: the format's own).
        overwrite: If True, replace an existing output.
        skip_existing: If True, leave an existing output untouched.
        error_on_conflict: If True, fail instead of renaming a clashing output.
        threads: Worker threads for zstd and xz (default: one per core).
        quiet: If True, suppress all output.
    """
    if sum([overwrite, skip_existing, error_on_conflict]) > 1:
        fail(
            "Error: --overwrite, --skip-existing and --error-on-conflict "
            "are mutually exclusive",
            EXIT_USAGE,
        )

    if overwrite:
        overwrite_mode = OverwriteMode.OVERWRITE
    elif skip_existing:
        overwrite_mode = OverwriteMode.SKIP
    elif error_on_conflict:
        overwrite_mode = OverwriteMode.ERROR
    else:
        overwrite_mode = OverwriteMode.RENAME

    try:
        if not inputs:
            fail("Error: No input paths given", EXIT_USAGE)

        algo_lower: str | None = algo.lower() if algo else None
        request = CompressRequest(
            inputs=inputs,
            output=output,
            format=format,
            algo=None if algo_lower == "auto" else algo_lower,
            strategy=strategy.lower(),
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
                _archive_many(job, quiet)
            else:
                _compress_one_file(job, quiet)

        sys.exit(run_all([job], run_one, "Compression"))

    except KeyboardInterrupt:
        cancelled("Compression")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Compression error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["c", "comp", "archive", "a", "ar"])(compress)

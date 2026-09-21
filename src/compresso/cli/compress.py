"""The `compress` command, which also answers to `archive`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import CompressionJob, CompressionOptions
from ..frontend.archive_api import ArchiveJob, ArchiveOptions, OverwriteMode
from ._app import app
from ._dispatch import (
    DEFAULT_ARCHIVE_FORMAT,
    default_archive_output,
    format_is_single_file,
    infer_format,
    is_archive_format,
    resolve_single_output,
)
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


def _compress_one_file(
    source: Path,
    output: Path | None,
    fmt: str | None,
    algo: str | None,
    strategy: str,
    level: int | None,
    overwrite: OverwriteMode,
    quiet: bool,
) -> None:
    """Compress a single file into a one-file container.

    Args:
        source: The path to the file to compress.
        output: The path to the output file or directory.
        fmt: The format to use for compression.
        algo: The algorithm to use for compression.
        strategy: The strategy to use for compression.
        level: The compression level to use.
        overwrite: What to do when the destination file already exists.
        quiet: Whether to suppress progress output.
    """
    algo_lower: str | None = algo.lower() if algo else None
    options = CompressionOptions(
        algo=None if algo_lower == "auto" else algo_lower,
        strategy=strategy.lower(),
        level=level,
        format=fmt,
        overwrite=overwrite,
    )

    job = CompressionJob.from_file(src=source, dest=None, options=options)
    # Planned once to learn the default name, then repointed if `-o` moves it
    dest: Path = resolve_single_output(output, job.plan.dest)
    if dest != job.plan.dest:
        job = CompressionJob.from_file(src=source, dest=dest, options=options)

    plan = job.plan

    if not plan.can_compress:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if not quiet:
        lines: list[str] = [
            f"Compressing: {plan.src}",
            f"Output:      {plan.dest}",
            f"Algorithm:   {plan.backend_name}",
        ]
        if not fmt:
            lines.append(f"Strategy:    {strategy}")
        if level is not None:
            lines.append(f"Level:       {level}")
        print("\n".join(lines) + "\n")

    start_time: float = time.time()

    with progress_bar(
        "Compressing",
        plan.input_size,
        enabled=not quiet and plan.input_size > BAR_THRESHOLD,
    ) as on_progress:
        result = job.run(progress=on_progress)

    elapsed: float = time.time() - start_time

    exit_for_result(result, "Compression")

    # RENAME may have written somewhere other than `dest`; job.plan is updated
    # in place on success to reflect the path actually written
    actual_dest: Path = job.plan.dest
    compressed_size: int = actual_dest.stat().st_size
    ratio: int | float = (
        (compressed_size / plan.input_size) * 100 if plan.input_size > 0 else 0
    )
    speed_mbs: int | float = (
        (plan.input_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
    )

    if not quiet:
        print()
        succeed("Compression successful!\n")
        if actual_dest != dest:
            print(f"  Renamed to:      {actual_dest}")
        print(
            f"  Original size:   {format_size(size_bytes=plan.input_size)}\n"
            f"  Compressed size: {format_size(size_bytes=compressed_size)}\n"
            f"  Ratio:           {ratio:.1f}% of original\n"
            f"  Time:            {format_time(seconds=elapsed)}\n"
            f"  Speed:           {speed_mbs:.2f} MB/s\n"
            f"  Saved:           {format_size(size_bytes=plan.input_size - compressed_size)}\n"
        )


def _archive_many(
    sources: list[Path],
    output: Path | None,
    fmt: str,
    level: int | None,
    overwrite: OverwriteMode,
    quiet: bool,
) -> None:
    """Pack several paths, or a directory, into one archive.

    Args:
        sources: The paths to the files or directories to archive.
        output: The path to the output file or directory.
        fmt: The format to use for compression.
        level: The compression level to use.
        overwrite: What to do when the destination archive already exists.
        quiet: Whether to suppress progress output.
    """
    destination: Path = (
        output if output is not None else default_archive_output(sources[0], fmt)
    )

    options = ArchiveOptions(
        format=fmt.lower(), compression_level=level, overwrite=overwrite
    )
    job = ArchiveJob.from_paths(
        sources=[str(object=s) for s in sources], output=destination, options=options
    )
    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if not quiet:
        print(
            f"Archiving:   {plan.entry_count} source(s)\n"
            f"Output:      {plan.output}\n"
            f"Format:      {plan.options.format}\n"
            f"Input size:  {format_size(size_bytes=plan.total_input_size)}\n"
        )

    start_time: float = time.time()

    # A format with a codec stage reads the data twice, so the job reports a
    # larger total than this once it starts; the bar follows it
    with progress_bar(
        "Archiving",
        plan.total_input_size,
        enabled=not quiet and plan.total_input_size > BAR_THRESHOLD,
    ) as on_progress:
        result = job.run(progress=on_progress)

    elapsed: float = time.time() - start_time

    exit_for_result(result, "Archive")

    # RENAME may have written somewhere other than `destination`; job.plan is
    # updated in place on success to reflect the path actually written
    actual_output: Path = job.plan.output
    archive_size: int = actual_output.stat().st_size

    if not quiet:
        print()
        succeed("Archive created!")
        if actual_output != destination:
            print(f"  Renamed to:   {actual_output}")
        print(
            f"  Input size:   {format_size(size_bytes=plan.total_input_size)}\n"
            f"  Archive size: {format_size(size_bytes=archive_size)}\n"
            f"  Time:         {format_time(seconds=elapsed)}\n"
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
        app.Option("--level", "-l", min=0, max=9, help="Compression level (0-9)"),
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
        error_on_conflict: If True, fail instead of renaming a clashing
            output.
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

        chosen: str | None = format or infer_format(output)
        many: bool = len(inputs) > 1 or any(p.is_dir() for p in inputs)

        if format_is_single_file(chosen) and many:
            fail(
                f"Error: {chosen} holds a single file; "
                f"use an archive format such as tar.{chosen} for several",
                EXIT_USAGE,
            )

        if is_archive_format(chosen) or (many and chosen is None):
            _archive_many(
                sources=inputs,
                output=output,
                fmt=chosen or DEFAULT_ARCHIVE_FORMAT,
                level=level,
                overwrite=overwrite_mode,
                quiet=quiet,
            )

        else:
            _compress_one_file(
                source=inputs[0],
                output=output,
                fmt=chosen,
                algo=algo,
                strategy=strategy,
                level=level,
                overwrite=overwrite_mode,
                quiet=quiet,
            )

    except KeyboardInterrupt:
        cancelled("Compression")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Compression error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["c", "comp", "archive", "a", "ar"])(compress)

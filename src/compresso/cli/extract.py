"""The `extract` command, which also answers to `decompress`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import DecompressionJob
from ..frontend.archive_api import (
    ArchiveEntry,
    ExtractJob,
    ExtractOptions,
    OverwriteMode,
)
from ._app import app
from ._dispatch import looks_like_archive, resolve_single_output
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


def list_entries(entries: list[ArchiveEntry]) -> None:
    """Print one line per entry: its size, then its path.

    Directories and symlinks leave the size blank, a symlink is followed by
    its target, and each entry is indented once for every directory above it
    that was already listed.

    Args:
        entries: The archive's entries, in archive order.
    """
    listed_dirs: set[str] = set()

    for entry in entries:
        parts = entry.path.rstrip("/").split("/")
        depth = sum("/".join(parts[:i]) in listed_dirs for i in range(1, len(parts)))

        if entry.is_dir:
            listed_dirs.add("/".join(parts))

        # A symlink's stored size is the length of its target, not file data
        size = (
            ""
            if entry.is_dir or entry.is_symlink
            else format_size(size_bytes=entry.size)
        )
        target = f" -> {entry.link_target}" if entry.is_symlink else ""

        # "1023.99 KB" is the widest format_size gives
        app.echo(message=f"{size:>10}  {'  ' * depth}{entry.path}{target}")


def _extract_archive(
    source: Path,
    output_dir: Path | None,
    list_only: bool,
    options: ExtractOptions,
    quiet: bool,
) -> None:
    """Unpack one archive.

    Args:
        source: The path to the archive to extract.
        output_dir: The directory to extract to.
        list_only: Whether to list entries only.
        options: The extract options.
        quiet: Whether to suppress output.
    """
    job = ExtractJob.from_archive(
        archive=source, output_dir=output_dir, options=options
    )
    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if list_only:
        list_entries(job.list_contents())
        return

    if not quiet:
        app.echo(message=f"Extracting: {plan.archive}")
        app.echo(message=f"Output dir: {plan.output_dir}")
        app.echo(message=f"Entries:    {len(plan.entries)}")
        app.echo()

    start_time: float = time.time()

    # Progress covers the bytes written out; a compressed archive also reports
    # the pass that decompresses it, so the job's own total wins
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


def _decompress_one_file(
    source: Path, output: Path | None, list_only: bool, quiet: bool
) -> None:
    """Unpack one single-file container: a `.comp` or a standalone codec.

    Args:
        source: The path to the single-file container.
        output: The output path.
        list_only: Whether to list entries only.
        quiet: Whether to suppress output.
    """
    if list_only:
        fail(
            f"Error: {source.name} holds a single file, so there is nothing to list",
            EXIT_USAGE,
        )

    job = DecompressionJob.from_file(src=source, dest=None)
    dest: Path = resolve_single_output(output, job.plan.dest)
    if dest != job.plan.dest:
        job = DecompressionJob.from_file(src=source, dest=dest)

    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    insp = plan.inspection

    if not quiet:
        app.echo(message=f"Decompressing: {plan.src}")
        app.echo(message=f"Output:        {plan.dest}")
        app.echo(message=f"Format:        {plan.format or insp.algo_name}")
        if insp.orig_size:
            app.echo(message=f"Original size: {format_size(size_bytes=insp.orig_size)}")
        app.echo()

    start_time: float = time.time()

    # Progress counts the compressed bytes read, so the source's size is the
    # denominator rather than the size it expands to
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


def extract(
    inputs: Annotated[list[Path], app.Argument(help="Archives or compressed files")],
    output: Annotated[
        Path | None,
        app.Option(
            "--output-dir",
            "--output",
            "-o",
            help="Where to write (default: beside the input)",
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
    """Unpack archives and compressed files.
    \f

    Each input is identified by its own magic bytes rather than its name: an
    archive is unpacked into a directory, and a single-file container, a
    Compresso `.comp`, or a standalone `.gz`/`.bz2`/`.xz`/`.zst`/`.lz4`, is
    decompressed to one file.

    The result is written beside its input unless `-o` is provided.

    Extraction refuses to touch an existing file unless either `--overwrite`
    or `--skip-existing` is explicitly used.

    Args:
        inputs: The archives or compressed files to unpack.
        output: Where to write (default: beside the input); a directory for
            archives, and for one file either a directory to put it in or the
            path to write.
        list_only: If True, list archive contents without extracting.
        overwrite: If True, replace files that already exist.
        skip_existing: If True, leave files that already exist untouched.
        max_total_size: Cap on total extracted bytes (default: no cap).
        quiet: If True, suppress all output.
    """
    if overwrite and skip_existing:
        fail(
            "Error: --overwrite and --skip-existing are mutually exclusive",
            EXIT_USAGE,
        )

    if not inputs:
        fail("Error: No input files given", EXIT_USAGE)

    if overwrite:
        mode = OverwriteMode.OVERWRITE
    elif skip_existing:
        mode = OverwriteMode.SKIP
    else:
        mode = OverwriteMode.ERROR

    options = ExtractOptions(overwrite=mode, max_total_size=max_total_size or 0)

    try:
        for source in inputs:
            if looks_like_archive(source):
                _extract_archive(source, output, list_only, options, quiet)

            else:
                _decompress_one_file(source, output, list_only, quiet)

    except KeyboardInterrupt:
        cancelled("Extraction")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Extraction error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["x", "ex", "decompress", "d", "decomp"])(extract)

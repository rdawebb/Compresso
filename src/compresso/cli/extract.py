"""The `extract` command, which also answers to `decompress`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from .._core import BackendError, Error, HeaderError
from ..frontend.api import DecompressionJob
from ..frontend.archive_api import (
    ExtractJob,
    ExtractOptions,
    OverwriteMode,
    SymlinkMode,
)
from ..frontend.dispatch import ExtractRequest, plan_extract
from ._app import app
from ._render import (
    BAR_THRESHOLD,
    EXIT_USAGE,
    cancelled,
    exit_for_result,
    fail,
    progress_bar,
)
from ._render.info import list_entries
from ._render.jobs import (
    decompress_plan,
    decompress_result,
    extract_plan,
    extract_result,
)


def _extract_archive(job: ExtractJob, list_only: bool, quiet: bool) -> None:
    """Unpack one archive.

    Args:
        job: The planned job.
        list_only: Whether to list entries only.
        quiet: Whether to suppress output.
    """
    plan = job.plan

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if list_only:
        list_entries(job.list_contents())
        return

    if not quiet:
        extract_plan(plan)

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
        extract_result(plan, elapsed)


def _decompress_one_file(job: DecompressionJob, list_only: bool, quiet: bool) -> None:
    """Unpack one single-file container: a `.comp` or a standalone codec.

    Args:
        job: The planned job.
        list_only: Whether to list entries only.
        quiet: Whether to suppress output.
    """
    plan = job.plan

    if list_only:
        fail(
            f"Error: {plan.src.name} holds a single file, so there is nothing to list",
            EXIT_USAGE,
        )

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    if not quiet:
        decompress_plan(plan)

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

    # RENAME may have written somewhere other than `plan.dest`; job.plan is
    # replaced on success with the path actually written
    if not quiet:
        decompress_result(plan, job.plan.dest, compressed_size, elapsed)


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
    error_on_conflict: Annotated[
        bool,
        app.Option(
            "--error-on-conflict", help="Fail instead of renaming a clashing file"
        ),
    ] = False,
    max_total_size: Annotated[
        int | None,
        app.Option(
            "--max-total-size",
            help="Refuse archives extracting to more than this many bytes",
        ),
    ] = None,
    same_permissions: Annotated[
        bool,
        app.Option(
            "--same-permissions",
            "-p",
            help="Restore modes exactly, keeping setuid/setgid/sticky and ignoring the umask",
        ),
    ] = False,
    symlinks: Annotated[
        SymlinkMode | None,
        app.Option(
            "--symlinks",
            case_sensitive=False,
            help="Refuse symlinks, create those staying inside the output, "
            "or write each as a file holding its target "
            "(default: file for zip, deny otherwise)",
        ),
    ] = None,
    threads: Annotated[
        int,
        app.Option(
            "--threads",
            "-T",
            min=0,
            help="Worker threads for xz, or 0 for one per core",
        ),
    ] = 0,
    name: Annotated[
        bool,
        app.Option(
            "--name",
            "-N",
            help="Name and date a .gz file's output as its header does",
        ),
    ] = False,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Unpack archives and compressed files.
    \f

    Each input is identified by its own magic bytes rather than its name: an
    archive is unpacked into a directory, and a single-file container, a
    Compresso `.comp`, or a standalone `.gz`/`.bz2`/`.xz`/`.zst`/`.lz4`/`.sz`,
    is decompressed to one file.

    The result is written beside its input unless `-o` is provided.

    By default, a clashing name gets a platform-native numbered sibling
    (`name 2.ext` on macOS, `name (2).ext` elsewhere); use `--overwrite`,
    `--skip-existing`, or `--error-on-conflict` to change that.

    Args:
        inputs: The archives or compressed files to unpack.
        output: Where to write (default: beside the input); a directory for
            archives, and for one file either a directory to put it in or the
            path to write.
        list_only: If True, list archive contents without extracting.
        overwrite: If True, replace files that already exist.
        skip_existing: If True, leave files that already exist untouched.
        error_on_conflict: If True, fail instead of renaming a clashing file.
        max_total_size: Cap on total extracted bytes (default: no cap).
        same_permissions: If True, restore archive entries' modes exactly;
            single-file outputs are unaffected.
        symlinks: What to do with a symlink entry (default: write a zip's as
            files, refuse any other archive's).
        threads: Worker threads for xz (default: one per core).
        name: If True, name a gzip file's output after the name its header
            stores, and give it the stored modification time, if any.
        quiet: If True, suppress all output.
    """
    if sum([overwrite, skip_existing, error_on_conflict]) > 1:
        fail(
            "Error: --overwrite, --skip-existing and --error-on-conflict "
            "are mutually exclusive",
            EXIT_USAGE,
        )

    if not inputs:
        fail("Error: No input files given", EXIT_USAGE)

    if overwrite:
        mode = OverwriteMode.OVERWRITE
    elif skip_existing:
        mode = OverwriteMode.SKIP
    elif error_on_conflict:
        mode = OverwriteMode.ERROR
    else:
        mode = OverwriteMode.RENAME

    options = ExtractOptions(
        overwrite=mode,
        max_total_size=max_total_size or 0,
        exact_permissions=same_permissions,
        symlinks=symlinks,
        threads=threads,
    )

    request = ExtractRequest(
        inputs=inputs, output=output, options=options, restore_name=name
    )

    try:
        for job in plan_extract(request):
            if isinstance(job, ExtractJob):
                _extract_archive(job, list_only, quiet)

            else:
                _decompress_one_file(job, list_only, quiet)

    except KeyboardInterrupt:
        cancelled("Extraction")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Extraction error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["x", "ex", "decompress", "d", "decomp"])(extract)

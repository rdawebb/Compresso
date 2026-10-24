"""The `extract` command, unpacking archives and compressed files."""

from __future__ import annotations

import sys
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
from ._render import EXIT_USAGE, cancelled, fail
from ._render.jobs import (
    decompress_plan,
    decompress_result,
    extract_plan,
    extract_result,
)
from ._run import InputFailed, conflict_mode, require_files, run_all, run_job


def _extract_archive(job: ExtractJob, quiet: bool) -> None:
    """Unpack one archive.

    Args:
        job: The planned job.
        quiet: Whether to suppress output.
    """
    plan = job.plan

    if not plan.can_run:
        raise InputFailed(f"{plan.archive}: {plan.reason_if_unavailable}")

    if not quiet:
        extract_plan(plan)

    # Progress covers the bytes written out; a compressed archive also reports
    # the pass that decompresses it, so the job's own total wins
    elapsed: float = run_job(
        job,
        plan.archive,
        "Extracting",
        sum(entry.size for entry in plan.entries),
        quiet=quiet,
        what="Extraction",
    )

    if not quiet:
        extract_result(plan, elapsed)


def _decompress_one_file(job: DecompressionJob, quiet: bool) -> None:
    """Unpack one single-file container: a `.comp` or a standalone codec.

    Args:
        job: The planned job.
        quiet: Whether to suppress output.
    """
    plan = job.plan

    if not plan.can_run:
        raise InputFailed(f"{plan.src}: {plan.reason_if_unavailable}")

    if not quiet:
        decompress_plan(plan)

    # Progress counts the compressed bytes read, so the source's size is the
    # denominator rather than the size it expands to
    compressed_size: int = plan.src.stat().st_size

    elapsed: float = run_job(
        job,
        plan.src,
        "Decompressing",
        compressed_size,
        quiet=quiet,
        what="Decompression",
    )

    # RENAME may have written somewhere other than `plan.dest`; job.plan is
    # replaced on success with the path actually written
    if not quiet:
        decompress_result(plan, job.plan.dest, compressed_size, elapsed)


def extract(
    inputs: Annotated[
        list[Path],
        app.Argument(help="Archives or compressed files", readable=False),
    ],
    output: Annotated[
        Path | None,
        app.Option(
            "--output-dir",
            "--output",
            "-o",
            help="Where to write (default: beside the input)",
        ),
    ] = None,
    on_conflict: Annotated[
        OverwriteMode | None,
        app.Option(
            "--on-conflict",
            case_sensitive=False,
            help="What to do when a file exists",
            show_default="rename",
        ),
    ] = None,
    force: Annotated[
        bool,
        app.Option("--force", "-f", help="Replace files that already exist"),
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
    (`name 2.ext` on macOS, `name (2).ext` elsewhere); use `--on-conflict` or
    `-f` to change that.

    Args:
        inputs: The archives or compressed files to unpack.
        output: Where to write (default: beside the input); a directory for
            archives, and for one file either a directory to put it in or the
            path to write.
        on_conflict: What to do when a file exists (default: rename).
        force: If True, replace files that already exist.
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
    mode = conflict_mode(on_conflict, force)

    if not inputs:
        fail("Error: No input files given", EXIT_USAGE)

    require_files(inputs)

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

    def run_one(job: ExtractJob | DecompressionJob) -> None:
        if isinstance(job, ExtractJob):
            _extract_archive(job, quiet)

        else:
            _decompress_one_file(job, quiet)

    try:
        sys.exit(run_all(plan_extract(request), run_one, "Extraction"))

    except KeyboardInterrupt:
        cancelled("Extraction")

    except (Error, HeaderError, BackendError) as e:
        fail(f"Extraction error: {e}")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["x"])(extract)

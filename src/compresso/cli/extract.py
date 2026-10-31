"""The `extract` command, unpacking archives and compressed files."""

from __future__ import annotations

import re
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
from ._render import EXIT_USAGE, cancelled, fail, report_warning
from ._render.jobs import (
    decompress_plan,
    decompress_result,
    extract_plan,
    extract_result,
    skipped,
    summary,
)
from ._run import InputFailed, conflict_mode, require_files, run_all, run_job

_SIZE = re.compile(r"(\d+)(?:([kmgt])(?:i?b)?|b)?", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(value: str) -> int:
    """Parse a byte count with an optional binary suffix, e.g. `500M` or `1KB`.

    Args:
        value: The text given.

    Returns:
        The count in bytes.

    Raises:
        ValueError: If `value` isn't a size, which the parser reports as a
            usage error.
    """
    match = _SIZE.fullmatch(value.strip())
    if not match:
        raise ValueError(value)

    count, unit = match.groups()

    return int(count) * _UNITS[(unit or "").lower()]


def _extract_archive(job: ExtractJob, quiet: bool, verbose: bool) -> None:
    """Unpack one archive.

    Args:
        job: The planned job.
        quiet: Whether to suppress all output.
        verbose: Whether to print the plan and stats blocks.
    """
    plan = job.plan

    if not plan.can_run:
        raise InputFailed(f"{plan.archive}: {plan.reason_if_unavailable}")

    if verbose:
        extract_plan(plan)

    # Progress covers the bytes written out; a compressed archive also reports
    # the pass that decompresses it, so the job's own total wins
    total: int = sum(entry.size for entry in plan.entries)
    elapsed: float = run_job(
        job, plan.archive, "Extracting", total, quiet=quiet, what="Extraction"
    )

    # Names and includes the filter dropped still count as found
    if plan.options.exclude and not plan.entries and not quiet:
        report_warning(f"{plan.archive}: nothing left to extract")

    if verbose:
        extract_result(plan, elapsed)

    elif not quiet:
        summary(
            plan.archive, plan.archive.stat().st_size, total, elapsed, None, ratio=False
        )


def _decompress_one_file(job: DecompressionJob, quiet: bool, verbose: bool) -> None:
    """Unpack one single-file container: a `.comp` or a standalone codec.

    Args:
        job: The planned job.
        quiet: Whether to suppress all output.
        verbose: Whether to print the plan and stats blocks.
    """
    plan = job.plan

    if not plan.can_run:
        raise InputFailed(f"{plan.src}: {plan.reason_if_unavailable}")

    if verbose:
        decompress_plan(plan)

    # Progress counts the compressed bytes read, so the source's size is the
    # denominator rather than the size it expands to
    compressed_size: int = plan.src.stat().st_size
    skips: bool = plan.overwrite is OverwriteMode.SKIP and plan.dest.exists()

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
    written: Path = job.plan.dest
    if verbose:
        decompress_result(plan, written, compressed_size, elapsed)

    elif quiet:
        return

    elif skips:
        skipped(plan.dest)

    else:
        renamed: Path | None = written if written != plan.dest else None
        summary(
            plan.src,
            compressed_size,
            written.stat().st_size,
            elapsed,
            renamed,
            ratio=False,
        )


def extract(
    inputs: Annotated[
        list[Path],
        app.Argument(help="Archives or compressed files", readable=False),
    ],
    directory: Annotated[
        Path | None,
        app.Option(
            "--directory",
            "-C",
            help="Directory to write into (default: beside each input)",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        app.Option(
            "--output",
            "-o",
            help="File to write, for one compressed file that isn't an archive",
        ),
    ] = None,
    entry: Annotated[
        list[str] | None,
        app.Option(
            "--entry",
            "-e",
            help="Extract only this entry, or the directory and all below it; "
            "repeatable",
        ),
    ] = None,
    include: Annotated[
        list[str] | None,
        app.Option(
            "--include",
            help="Extract entries matching this glob, as well as any --entry; "
            "repeatable",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        app.Option(
            "--exclude",
            help="Skip entries matching this glob, overriding --entry and "
            "--include; repeatable",
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
    max_size: Annotated[
        int | None,
        app.Option(
            "--max-size",
            metavar="SIZE",
            parser=parse_size,
            help="Refuse archives extracting to more than this, e.g. 500M or 10G",
        ),
    ] = None,
    max_depth: Annotated[
        int,
        app.Option(
            "--max-depth",
            min=0,
            help="Refuse entries nested deeper than this, or 0 for no limit",
        ),
    ] = 32,
    same_permissions: Annotated[
        bool,
        app.Option(
            "--same-permissions",
            "-p",
            help="Restore modes exactly, keeping setuid/setgid/sticky and ignoring the umask",
        ),
    ] = False,
    no_same_permissions: Annotated[
        bool,
        app.Option("--no-same-permissions", help="Don't restore modes"),
    ] = False,
    touch: Annotated[
        bool,
        app.Option("--touch", "-m", help="Don't restore modification times"),
    ] = False,
    overwrite_dir: Annotated[
        bool,
        app.Option(
            "--overwrite-dir",
            help="Also restore the mode and time of directories that already exist",
        ),
    ] = False,
    symlinks: Annotated[
        SymlinkMode | None,
        app.Option(
            "--symlinks",
            case_sensitive=False,
            help="Refuse, create (non-escaping), or write as a file",
            show_default="file for zip, deny otherwise",
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
    verbose: Annotated[
        bool,
        app.Option(
            "--verbose",
            "-v",
            help="Show the plan and full stats",
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

    The result is written beside its input unless `-C` or `-o` is provided.

    By default, a clashing name gets a platform-native numbered sibling
    (`name 2.ext` on macOS, `name (2).ext` elsewhere); use `--on-conflict` or
    `-f` to change that.

    Each input gets one summary line; `-v` shows the plan and full stats
    instead, and `-q` nothing at all.

    Args:
        inputs: The archives or compressed files to unpack.
        directory: The directory to write into (default: beside each input).
        output: The file to write, for a single input that isn't an archive.
        entry: Entry paths to extract from each archive (default: all); a
            path matching no entry fails that archive after the rest is
            extracted.
        include: Globs selecting entries as well as any `entry` names; one
            matching no entry fails that archive likewise.
        exclude: Globs dropping entries, overriding `entry` and `include`; a
            run left with nothing to extract warns, but still succeeds.
        on_conflict: What to do when a file exists (default: rename).
        force: If True, replace files that already exist.
        max_size: Cap on total extracted bytes (default: no cap).
        max_depth: Cap on an entry path's depth, or 0 for none.
        same_permissions: If True, restore archive entries' modes exactly;
            single-file outputs are unaffected.
        no_same_permissions: If True, don't restore archive entries' modes.
        touch: If True, don't restore archive entries' modification times.
        overwrite_dir: If True, directories that already exist also get the
            archive's mode and modification time.
        symlinks: What to do with a symlink entry (default: write a zip's as
            files, refuse any other archive's).
        threads: Worker threads for xz (default: one per core).
        name: If True, name a gzip file's output after the name its header
            stores, and give it the stored modification time, if any.
        verbose: If True, show the plan and full stats.
        quiet: If True, suppress all output.
    """
    mode = conflict_mode(on_conflict, force)
    if verbose and quiet:
        fail("Error: -v contradicts -q", EXIT_USAGE)

    if not inputs:
        fail("Error: No input files given", EXIT_USAGE)

    if output and directory:
        fail("Error: -o contradicts -C", EXIT_USAGE)

    if output and len(inputs) > 1:
        fail("Error: -o names one file; use -C for several inputs", EXIT_USAGE)

    if same_permissions and no_same_permissions:
        fail("Error: -p contradicts --no-same-permissions", EXIT_USAGE)

    require_files(inputs)

    options = ExtractOptions(
        overwrite=mode,
        max_total_size=max_size or 0,
        max_depth=max_depth,
        preserve_permissions=not no_same_permissions,
        exact_permissions=same_permissions,
        preserve_timestamps=not touch,
        overwrite_dir_metadata=overwrite_dir,
        symlinks=symlinks,
        threads=threads,
        include=include or (),
        exclude=exclude or (),
    )

    request = ExtractRequest(
        inputs=inputs,
        directory=directory,
        output=output,
        files=entry,
        options=options,
        restore_name=name,
    )

    def run_one(job: ExtractJob | DecompressionJob) -> None:
        if isinstance(job, ExtractJob):
            _extract_archive(job, quiet, verbose)

        else:
            # An archive creates a missing `-C` itself; a single file needs it made
            if directory:
                directory.mkdir(parents=True, exist_ok=True)

            _decompress_one_file(job, quiet, verbose)

    if not (quiet or verbose):
        print()

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

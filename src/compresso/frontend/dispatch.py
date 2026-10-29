"""Dispatch rules for the smart `compress` and `extract` commands."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path

from .._core import detect_format, format_is_archive
from ._formats import (
    DEFAULT_ARCHIVE_FORMAT,
    canonical_standalone_format,
    infer_format,
)
from .api import CompressionJob, CompressionOptions, DecompressionJob
from .archive_api import (
    ArchiveJob,
    ArchiveOptions,
    ExtractJob,
    ExtractOptions,
    OverwriteMode,
)


def looks_like_archive(path: Path) -> bool:
    """Return whether `path`'s own bytes say it holds many entries.

    Args:
        path: The path to check.

    Returns:
        True if the path looks like an archive, False otherwise.
    """
    try:
        return bool(format_is_archive(detect_format(path)))

    except Exception:  # noqa: BLE001 - unreadable means not an archive
        return False


def is_archive_format(fmt: str | None) -> bool:
    """Return whether `fmt` names a container that holds many entries.

    Args:
        fmt: The format string to check.

    Returns:
        True if `fmt` names an archive format, False otherwise.
    """
    # Includes currently unwritable formats (e.g. 7z)
    return fmt is not None and format_is_archive(fmt.lower())


def default_archive_output(first_input: Path, fmt: str) -> Path:
    """Name an archive the caller did not name.

    Args:
        first_input: The first source path, which the archive is named after.
        fmt: The archive format, used as the suffix.

    Returns:
        A path beside the input, e.g. `src/` in tar.zst becomes `src.tar.zst`.
    """
    # A trailing separator would otherwise make the name empty
    stem: str = first_input.resolve().name

    return first_input.resolve().parent / f"{stem}.{fmt}"


def format_is_single_file(fmt: str | None) -> bool:
    """Return whether `fmt` names a container holding exactly one file.

    Args:
        fmt: The format string to check.

    Returns:
        True if `fmt` names a single-file format, False otherwise.
    """
    return canonical_standalone_format(fmt) is not None


@dataclass(frozen=True)
class CompressRequest:
    """What `compress` was asked to do, already parsed.

    Attributes:
        inputs: The files and directories to compress.
        output: The `-o` value, or None to name the output after the first input.
        format: The container asked for, or None to infer it.
        algo: The `.comp` algorithm, or None for the strategy's default.
        strategy: The `.comp` strategy, or None for the default.
        level: The compression level, or None for the format's own.
        overwrite: What to do when the output already exists.
        threads: Worker threads, or 0 for one per core.
    """

    inputs: list[Path]
    output: Path | None = None
    format: str | None = None
    algo: str | None = None
    strategy: str | None = None
    level: int | None = None
    overwrite: OverwriteMode = OverwriteMode.RENAME
    threads: int = 0


@dataclass(frozen=True)
class ExtractRequest:
    """What `extract` was asked to do, already parsed.

    Attributes:
        inputs: The archives and single-file containers to unpack.
        directory: The `-C` value, or None to write beside each input.
        output: The `-o` file, for a single non-archive input.
        files: The `-m` entry paths to extract from each archive, or
            None for all.
        options: The extraction policy; single-file inputs use only its
            `overwrite` and `threads`.
        restore_name: Whether to name and date a gzip file's output as its
            header does.
    """

    inputs: list[Path]
    directory: Path | None = None
    output: Path | None = None
    files: list[str] | None = None
    options: ExtractOptions = field(default_factory=ExtractOptions)
    restore_name: bool = False


def plan_compress(request: CompressRequest) -> CompressionJob | ArchiveJob:
    """Turn a compress request into a planned job.

    One file becomes a single-file container; a directory, several inputs, or
    an archive format becomes an archive.

    Args:
        request: The parsed request.

    Returns:
        The planned job, which may still be unable to run (see its plan),
        as when `algo` or `strategy` is given for anything but a `.comp`.

    Raises:
        ValueError: If a single-file format was asked for with several inputs
            or a directory.
    """
    fmt: str | None = request.format or infer_format(request.output)
    many: bool = len(request.inputs) > 1 or any(p.is_dir() for p in request.inputs)

    # Only the .comp container records its codec; the others fix their own
    comp_flag: str | None = "-a" if request.algo else "-s" if request.strategy else None

    if format_is_single_file(fmt) and many:
        raise ValueError(
            f"{fmt} holds a single file; "
            f"use an archive format such as tar.{fmt} for several"
        )

    if is_archive_format(fmt) or (many and fmt is None):
        fmt = fmt or DEFAULT_ARCHIVE_FORMAT
        output: Path = request.output or default_archive_output(request.inputs[0], fmt)
        archive_options = ArchiveOptions(
            format=fmt.lower(),
            compression_level=request.level,
            overwrite=request.overwrite,
            threads=request.threads,
        )

        archive_job = ArchiveJob.from_paths(
            sources=request.inputs, output=output, options=archive_options
        )
        if comp_flag:
            archive_job.plan = replace(
                archive_job.plan,
                can_run=False,
                reason_if_unavailable=f"{comp_flag} applies only to .comp, not {fmt}",
            )

        return archive_job

    source: Path = request.inputs[0]
    options = CompressionOptions(
        algo=request.algo,
        strategy=request.strategy or CompressionOptions.strategy,
        level=request.level,
        format=fmt,
        overwrite=request.overwrite,
        threads=request.threads,
    )

    job = CompressionJob.from_file(src=source, dest=request.output, options=options)

    if comp_flag and format_is_single_file(fmt):
        job.plan = replace(
            job.plan,
            can_compress=False,
            reason_if_unavailable=f"{comp_flag} applies only to .comp, not {fmt}",
        )

    return job


def plan_extract(request: ExtractRequest) -> list[ExtractJob | DecompressionJob]:
    """Turn an extract request into one planned job per input.

    Each input is dispatched on its own bytes, not its name: an archive is
    unpacked into a directory, anything else is decompressed to one file.

    Args:
        request: The parsed request.

    Returns:
        The planned jobs, in input order; each may still be unable to run.
    """
    jobs: list[ExtractJob | DecompressionJob] = []
    for source in request.inputs:
        if looks_like_archive(source):
            archive_job = ExtractJob.from_archive(
                archive=source,
                output_dir=request.directory,
                files=request.files,
                options=request.options,
            )
            if request.output:
                archive_job.plan = replace(
                    archive_job.plan,
                    can_run=False,
                    reason_if_unavailable="-o names one file; use -C for an archive",
                )

            jobs.append(archive_job)
            continue

        plan = partial(
            DecompressionJob.from_file,
            src=source,
            overwrite=request.options.overwrite,
            threads=request.options.threads,
            restore_name=request.restore_name,
        )
        if request.output or not request.directory:
            job: DecompressionJob = plan(dest=request.output)

        else:
            # Planned once to learn the default name, then moved into `-C`
            job = plan(dest=request.directory / plan(dest=None).plan.dest.name)

        if request.files:
            job.plan = replace(
                job.plan,
                can_run=False,
                reason_if_unavailable="--entry applies only to archives",
            )

        jobs.append(job)

    return jobs

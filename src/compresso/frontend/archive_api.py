"""Archive API for multi-file operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Self

from .._core import (
    Cancelled,
    CancelToken,
    check_level,
    create_archive,
    extract_archive,
    format_is_archive,
    list_archive_contents,
)
from .._levels import to_core_level
from ._formats import DEFAULT_ARCHIVE_FORMAT, infer_format
from ._job import JobResult, ProgressCallback, ThreadedJob, to_core_progress


@dataclass(frozen=True)
class ArchiveEntry:
    """One file, directory or link recorded in an archive.

    Attributes:
        path: The entry's path within the archive.
        size: Uncompressed size in bytes; 0 for a directory.
        is_dir: Whether the entry is a directory.
        is_symlink: Whether the entry is a symbolic link.
        is_hardlink: Whether the entry is a hardlink, sharing the data of the
            earlier entry `link_target` names rather than holding its own.
        mtime: Modification time, in seconds since the epoch.
        mode: Unix permission bits.
        link_target: A symlink's stored target, or the archive path a
            hardlink shares data with; None for anything else.
        compressed_size: Stored size of this entry's data, or None for a
            container that compresses the whole stream at once.
        crc: CRC-32 of the uncompressed data, or None as above.
        method: The container's own compression method code, or None as above.
    """

    path: str
    size: int = 0
    is_dir: bool = False
    is_symlink: bool = False
    is_hardlink: bool = False
    mtime: float = 0.0
    mode: int = 0
    link_target: str | None = None
    compressed_size: int | None = None
    crc: int | None = None
    method: int | None = None


class OverwriteMode(StrEnum):
    """What archiving or extraction does when a destination already exists.

    Attributes:
        ERROR: Fail the operation.
        SKIP: Leave the existing file untouched.
        OVERWRITE: Replace the existing file.
        RENAME: Write to a platform-native numbered sibling instead (e.g.
            "name 2.ext" on macOS, "name (2).ext" elsewhere), leaving the
            existing file untouched.
    """

    ERROR = "error"
    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"


# The C policy uses ints; see `ExtractionPolicy` in csrc/archives.h. Archive
# creation applies the same codes to its single destination file.
_OVERWRITE_CODES: dict[OverwriteMode, int] = {
    OverwriteMode.ERROR: 0,
    OverwriteMode.SKIP: 1,
    OverwriteMode.OVERWRITE: 2,
    OverwriteMode.RENAME: 3,
}


class SymlinkMode(StrEnum):
    """What extraction does with a symlink entry.

    Attributes:
        DENY: Refuse the archive.
        ALLOW: Create the link, if its target stays inside the output directory.
        FILE: Write a regular file holding the link's target.
    """

    DENY = "deny"
    ALLOW = "allow"
    FILE = "file"


_SYMLINK_CODES: dict[SymlinkMode, int] = {
    SymlinkMode.DENY: 0,
    SymlinkMode.ALLOW: 1,
    SymlinkMode.FILE: 2,
}


@dataclass
class ArchiveOptions:
    """Represents the options for an archive operation.

    Attributes:
        format: The archive/compression format to write, or None to take it
            from the output's suffix, falling back to zip.
        compression_level: The compression level to use, or None for the
            format's default.
        overwrite: What to do when the destination archive already exists.
        threads: Worker threads for tar.zst and tar.xz, or 0 for one per core;
            the output is the same for any count.
    """

    format: str | None = None
    compression_level: int | None = None
    overwrite: OverwriteMode = OverwriteMode.RENAME
    threads: int = 0


@dataclass(frozen=True)
class ArchivePlan:
    """Holds a plan for creating an archive.

    Attributes:
        sources: Source paths to include in the archive.
        output: Destination archive path.
        options: Archive options.
        total_input_size: Sum of the sizes of the source inputs, in bytes.
        entry_count: Number of top-level source paths.
        can_run: Whether the archive operation can proceed.
        reason_if_unavailable: Reason the operation cannot proceed, if any.
    """

    sources: list[Path]
    output: Path
    options: ArchiveOptions

    total_input_size: int
    entry_count: int
    can_run: bool
    reason_if_unavailable: str | None


@dataclass
class ExtractOptions:
    """Represents the policy applied while extracting an archive.

    Attributes:
        overwrite: What to do when an entry's destination already exists.
        max_total_size: Cap on total extracted bytes; 0 means unlimited.
        max_depth: Maximum nesting depth of an entry path; 0 means unlimited.
        preserve_permissions: Whether to restore each entry's mode bits.
        exact_permissions: Whether those bits are restored exactly, setuid,
            setgid and sticky included; by default those are dropped and the
            umask applies, as tar does for a non-root user.
        preserve_timestamps: Whether to restore each entry's modification time.
        overwrite_dir_metadata: Whether a directory that already exists also
            gets the archive's mode and modification time; by default only directories
            the extraction creates get them; never applies with `OverwriteMode.SKIP`.
        symlinks: What to do with a symlink entry; by default a zip's are
            written as files and any other archive's are refused.
        threads: Worker threads for tar.xz, or 0 for one per core.
    """

    overwrite: OverwriteMode = OverwriteMode.RENAME
    max_total_size: int = 0
    max_depth: int = 32
    preserve_permissions: bool = True
    exact_permissions: bool = False
    preserve_timestamps: bool = True
    overwrite_dir_metadata: bool = False
    symlinks: SymlinkMode | None = None
    threads: int = 0


@dataclass(frozen=True)
class ExtractPlan:
    """Holds a plan for extracting an archive.

    Attributes:
        archive: Source archive path.
        output_dir: Directory to extract into.
        files: Optional subset of entry paths to extract (None = all).
        options: Extraction policy to apply.
        entries: Entries discovered in the archive.
        can_run: Whether the extract operation can proceed.
        reason_if_unavailable: Reason the operation cannot proceed, if any.
    """

    archive: Path
    output_dir: Path
    files: list[str] | None
    options: ExtractOptions

    entries: list[ArchiveEntry]
    can_run: bool
    reason_if_unavailable: str | None


def plan_archive(
    sources: Sequence[str | Path],
    output: str | Path,
    options: ArchiveOptions | None = None,
) -> ArchivePlan:
    """Plan an archive-creation operation.

    Args:
        sources: Source paths to archive.
        output: Destination archive path.
        options: Archive options. If None, defaults are used.

    Returns:
        ArchivePlan: The resulting plan.
    """
    if options is None:
        options = ArchiveOptions()

    source_paths: list[Path] = [Path(s) for s in sources]
    output_path = Path(output)

    fmt: str = options.format or infer_format(output_path) or DEFAULT_ARCHIVE_FORMAT
    options = replace(options, format=fmt)

    try:
        # Plain strings still work at runtime
        options = replace(options, overwrite=OverwriteMode(options.overwrite))

    except ValueError:
        return ArchivePlan(
            sources=source_paths,
            output=output_path,
            options=options,
            total_input_size=0,
            entry_count=len(source_paths),
            can_run=False,
            reason_if_unavailable=(
                f"Unknown overwrite mode: {options.overwrite!r} "
                f"(expected one of {', '.join(OverwriteMode)})"
            ),
        )

    if not source_paths:
        return ArchivePlan(
            sources=source_paths,
            output=output_path,
            options=options,
            total_input_size=0,
            entry_count=0,
            can_run=False,
            reason_if_unavailable="No source paths provided",
        )

    missing: list[Path] = [p for p in source_paths if not p.exists()]
    if missing:
        return ArchivePlan(
            sources=source_paths,
            output=output_path,
            options=options,
            total_input_size=0,
            entry_count=len(source_paths),
            can_run=False,
            reason_if_unavailable=f"Source path does not exist: {missing[0]}",
        )

    if not format_is_archive(fmt):
        return ArchivePlan(
            sources=source_paths,
            output=output_path,
            options=options,
            total_input_size=0,
            entry_count=len(source_paths),
            can_run=False,
            reason_if_unavailable=f"Format does not support archives: {fmt}",
        )

    if options.compression_level is not None:
        try:
            # The level belongs to the codec stage, or to the container itself
            check_level(options.compression_level, format=fmt)

        except ValueError as e:
            return ArchivePlan(
                sources=source_paths,
                output=output_path,
                options=options,
                total_input_size=0,
                entry_count=len(source_paths),
                can_run=False,
                reason_if_unavailable=str(e),
            )

    total_input_size: int = sum(
        f.stat().st_size for f in _iter_files(source_paths) if f.is_file()
    )

    return ArchivePlan(
        sources=source_paths,
        output=output_path,
        options=options,
        total_input_size=total_input_size,
        entry_count=len(source_paths),
        can_run=True,
        reason_if_unavailable=None,
    )


def _iter_files(paths: list[Path]):
    """Yield every file under the given paths, recursing into directories.

    Args:
        paths: List of paths to iterate over.

    Yields:
        Each file under the given paths.
    """
    for p in paths:
        if p.is_dir():
            yield from (f for f in p.rglob("*"))

        else:
            yield p


def plan_extraction(
    archive: str | Path,
    output_dir: str | Path | None = None,
    files: list[str] | None = None,
    options: ExtractOptions | None = None,
) -> ExtractPlan:
    """Plan an archive-extraction operation.

    Args:
        archive: Source archive path.
        output_dir: Directory to extract into. If None, the directory holding
            the archive.
        files: Optional subset of entry paths to extract. If None, extracts all.
            A name matching no entry fails the run with
            `IncompleteExtractionError`, after the rest is extracted.
        options: Extraction policy. If None, defaults are used.

    Returns:
        ExtractPlan: The resulting plan.
    """
    archive_path = Path(archive)
    out_dir = archive_path.resolve().parent if output_dir is None else Path(output_dir)
    opts = ExtractOptions() if options is None else options

    # Plain strings still work at runtime
    for field, mode in (("overwrite", OverwriteMode), ("symlinks", SymlinkMode)):
        value = getattr(opts, field)
        if value is None:
            continue
        try:
            opts = replace(opts, **{field: mode(value)})

        except ValueError:
            return ExtractPlan(
                archive=archive_path,
                output_dir=out_dir,
                files=files,
                options=opts,
                entries=[],
                can_run=False,
                reason_if_unavailable=(
                    f"Unknown {field} mode: {value!r} "
                    f"(expected one of {', '.join(mode)})"
                ),
            )

    if not archive_path.is_file():
        return ExtractPlan(
            archive=archive_path,
            output_dir=out_dir,
            files=files,
            options=opts,
            entries=[],
            can_run=False,
            reason_if_unavailable="Archive does not exist or is not a file",
        )

    try:
        entries: list[ArchiveEntry] = [
            ArchiveEntry(
                path=raw["path"],
                size=raw["size"],
                is_dir=raw["type"] == "dir",
                is_symlink=raw["type"] == "symlink",
                is_hardlink=raw["type"] == "hardlink",
                mtime=raw["mtime"],
                mode=raw["mode"],
                link_target=raw["link_target"],
                # Absent for a container without per-entry compression
                compressed_size=raw.get("compressed_size"),
                crc=raw.get("crc"),
                method=raw.get("method"),
            )
            for raw in list_archive_contents(archive_path)
        ]

    except Exception as e:  # noqa: BLE001 - surface as an unavailable plan
        return ExtractPlan(
            archive=archive_path,
            output_dir=out_dir,
            files=files,
            options=opts,
            entries=[],
            can_run=False,
            reason_if_unavailable=f"Cannot read archive: {e}",
        )

    return ExtractPlan(
        archive=archive_path,
        output_dir=out_dir,
        files=files,
        options=opts,
        entries=entries,
        can_run=True,
        reason_if_unavailable=None,
    )


class ArchiveJob(ThreadedJob[ArchivePlan]):
    """Job for creating an archive."""

    def __init__(self, plan: ArchivePlan) -> None:
        """Initialise archive job.

        Args:
            plan: The archive plan to execute.
        """
        self.plan: ArchivePlan = plan

    @classmethod
    def from_paths(
        cls,
        sources: Sequence[str | Path],
        output: str | Path,
        options: ArchiveOptions | None = None,
    ) -> Self:
        """Create an ArchiveJob from source paths and options.

        Args:
            sources: Source paths to archive.
            output: Destination archive path.
            options: Archive options. If None, defaults are used.

        Returns:
            ArchiveJob instance.
        """
        return cls(plan=plan_archive(sources, output, options))

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Create the archive.

        A destination that already exists is handled per `plan.options.overwrite`
        (default `RENAME`, the same numbered-sibling behavior extraction uses);
        when that renames the file, `self.plan.output` (and the plan on the
        returned `JobResult`) is updated to the path actually written.

        Args:
            progress: Optional progress callback, invoked with
                `(fraction, done_bytes, total_bytes)`.
            cancel: Optional `CancelToken`. Cancelling leaves no archive behind.

        Returns:
            JobResult indicating success or failure.
        """
        if not self.plan.can_run:
            return JobResult(
                ok=False,
                error=RuntimeError(
                    self.plan.reason_if_unavailable or "Cannot create archive"
                ),
                plan=self.plan,
            )

        # plan_archive resolves an unset format before a plan can run
        assert self.plan.options.format is not None

        total: int = self.plan.total_input_size
        try:
            actual_path = create_archive(
                self.plan.output,
                self.plan.options.format,
                self.plan.sources,
                to_core_level(self.plan.options.compression_level),
                overwrite=_OVERWRITE_CODES[self.plan.options.overwrite],
                threads=self.plan.options.threads,
                progress=to_core_progress(progress, total),
                cancel=cancel,
            )

            # RENAME may have picked a different name than what was requested;
            # keep `self.plan.output` truthful for callers that inspect it
            # after a successful run
            self.plan = replace(self.plan, output=Path(actual_path))
            return JobResult(ok=True, error=None, plan=self.plan)

        # Cancellation is a deliberate stop, not a failure; see
        # `CompressionJob.run`.
        except Cancelled:
            return JobResult(ok=False, error=None, plan=self.plan, cancelled=True)

        # `run` reports failure through JobResult rather than raising; see the
        # contract on `compresso.frontend._job.Job`.
        except Exception as e:  # noqa: BLE001
            return JobResult(ok=False, error=e, plan=self.plan)


class ExtractJob(ThreadedJob[ExtractPlan]):
    """Job for extracting an archive."""

    def __init__(self, plan: ExtractPlan) -> None:
        """Initialise extract job.

        Args:
            plan: ExtractPlan instance.
        """
        self.plan: ExtractPlan = plan

    @classmethod
    def from_archive(
        cls,
        archive: str | Path,
        output_dir: str | Path | None = None,
        files: list[str] | None = None,
        options: ExtractOptions | None = None,
    ) -> Self:
        """Create extract job from archive path.

        Args:
            archive: Path to the archive file.
            output_dir: Directory to extract to. If None, extracts beside the archive.
            files: Optional list of files to extract from the archive. If None, extracts all files.
            options: Extraction policy. If None, defaults are used.

        Returns:
            ExtractJob instance.
        """
        return cls(plan=plan_extraction(archive, output_dir, files, options))

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Extract the archive.

        Args:
            progress: Optional progress callback, invoked with
                `(fraction, done_bytes, total_bytes)` in bytes written.
            cancel: Optional `CancelToken`. Cancelling leaves already extracted
                entries in place, but removes any in-progress entry.

        Returns:
            JobResult indicating success or failure.
        """
        if not self.plan.can_run:
            return JobResult(
                ok=False,
                error=RuntimeError(
                    self.plan.reason_if_unavailable or "Cannot extract archive"
                ),
                plan=self.plan,
            )

        total: int = sum(entry.size for entry in self.plan.entries)
        try:
            options = self.plan.options
            self.plan.output_dir.mkdir(parents=True, exist_ok=True)
            extract_archive(
                self.plan.archive,
                self.plan.output_dir,
                self.plan.files or [],
                overwrite=_OVERWRITE_CODES[options.overwrite],
                max_total_size=options.max_total_size,
                max_depth=options.max_depth,
                preserve_permissions=options.preserve_permissions,
                exact_permissions=options.exact_permissions,
                preserve_timestamps=options.preserve_timestamps,
                overwrite_dir_metadata=options.overwrite_dir_metadata,
                allow_symlinks=(
                    -1 if options.symlinks is None else _SYMLINK_CODES[options.symlinks]
                ),
                threads=options.threads,
                progress=to_core_progress(progress, total),
                cancel=cancel,
            )

            return JobResult(ok=True, error=None, plan=self.plan)

        # Cancellation is a deliberate stop, not a failure; see
        # `CompressionJob.run`.
        except Cancelled:
            return JobResult(ok=False, error=None, plan=self.plan, cancelled=True)

        # `run` reports failure through JobResult rather than raising; see the
        # contract on `compresso.frontend._job.Job`
        except Exception as e:  # noqa: BLE001
            return JobResult(ok=False, error=e, plan=self.plan)

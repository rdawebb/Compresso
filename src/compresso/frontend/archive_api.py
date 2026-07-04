"""Archive API for multi-file operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .._core import create_archive, extract_archive, list_archive_contents
from ._job import JobResult, ProgressCallback

# Formats whose container cannot hold multiple entries
_NON_ARCHIVE_FORMATS = {"gz", "gzip", "bz2", "bzip2", "xz", "zst", "zstd", "lz4"}


@dataclass
class ArchiveEntry:
    """Represents a file or directory in an archive."""

    path: str
    size: int = 0
    is_dir: bool = False
    is_symlink: bool = False
    mtime: float = 0.0
    mode: int = 0
    link_target: str | None = None


@dataclass
class ArchiveOptions:
    """Represents the options for an archive operation."""

    format: str = "tar.zst"  # Default to tar with zstd
    compression_level: int | None = None
    preserve_permissions: bool = True
    preserve_timestamps: bool = True
    exclude_patterns: list[str] | None = None


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


@dataclass(frozen=True)
class ExtractPlan:
    """Holds a plan for extracting an archive.

    Attributes:
        archive: Source archive path.
        output_dir: Directory to extract into.
        files: Optional subset of entry paths to extract (None = all).
        entries: Entries discovered in the archive.
        can_run: Whether the extract operation can proceed.
        reason_if_unavailable: Reason the operation cannot proceed, if any.
    """

    archive: Path
    output_dir: Path
    files: list[str] | None

    entries: list[ArchiveEntry]
    can_run: bool
    reason_if_unavailable: str | None


def _format_supports_archive(fmt: str) -> bool:
    """Return whether a format's container can hold multiple entries.

    Args:
        fmt: The archive format string.

    Returns:
        True if the format supports multiple entries, False otherwise.
    """
    return fmt.lower() not in _NON_ARCHIVE_FORMATS


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

    if not _format_supports_archive(options.format):
        return ArchivePlan(
            sources=source_paths,
            output=output_path,
            options=options,
            total_input_size=0,
            entry_count=len(source_paths),
            can_run=False,
            reason_if_unavailable=f"Format does not support archives: {options.format}",
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
) -> ExtractPlan:
    """Plan an archive-extraction operation.

    Args:
        archive: Source archive path.
        output_dir: Directory to extract into. If None, the current directory.
        files: Optional subset of entry paths to extract. If None, extracts all.

    Returns:
        ExtractPlan: The resulting plan.
    """
    archive_path = Path(archive)
    out_dir = Path.cwd() if output_dir is None else Path(output_dir)

    if not archive_path.is_file():
        return ExtractPlan(
            archive=archive_path,
            output_dir=out_dir,
            files=files,
            entries=[],
            can_run=False,
            reason_if_unavailable="Archive does not exist or is not a file",
        )

    try:
        entries: list[ArchiveEntry] = [
            ArchiveEntry(path=name) for name in list_archive_contents(str(archive_path))
        ]
    except BaseException as e:  # noqa: BLE001 - surface as an unavailable plan
        return ExtractPlan(
            archive=archive_path,
            output_dir=out_dir,
            files=files,
            entries=[],
            can_run=False,
            reason_if_unavailable=f"Cannot read archive: {e}",
        )

    return ExtractPlan(
        archive=archive_path,
        output_dir=out_dir,
        files=files,
        entries=entries,
        can_run=True,
        reason_if_unavailable=None,
    )


class ArchiveJob:
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
    ) -> ArchiveJob:
        """Create an ArchiveJob from source paths and options.

        Args:
            sources: Source paths to archive.
            output: Destination archive path.
            options: Archive options. If None, defaults are used.

        Returns:
            ArchiveJob instance.
        """
        return cls(plan=plan_archive(sources, output, options))

    def run(self, progress: ProgressCallback | None = None) -> JobResult:
        """Create the archive.

        Args:
            progress: Optional progress callback.

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

        total: int = self.plan.total_input_size
        try:
            if progress:
                progress(0.0, 0, total)

            create_archive(
                str(self.plan.output),
                self.plan.options.format,
                [str(s) for s in self.plan.sources],
                self.plan.options.compression_level or -1,
            )

            if progress:
                progress(1.0, total, total)

            return JobResult(ok=True, error=None, plan=self.plan)

        except BaseException as e:
            return JobResult(ok=False, error=e, plan=self.plan)


class ExtractJob:
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
    ) -> ExtractJob:
        """Create extract job from archive path.

        Args:
            archive: Path to the archive file.
            output_dir: Directory to extract to. If None, extracts to current directory.
            files: Optional list of files to extract from the archive. If None, extracts all files.

        Returns:
            ExtractJob instance.
        """
        return cls(plan=plan_extraction(archive, output_dir, files))

    def list_contents(self) -> list[ArchiveEntry]:
        """List archive entry paths without extracting.

        Returns:
            List of ArchiveEntry instances.
        """
        return list(self.plan.entries)

    def run(self, progress: ProgressCallback | None = None) -> JobResult:
        """Extract the archive.

        Args:
            progress: Optional progress callback.

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

        total: int = len(self.plan.entries)
        try:
            if progress:
                progress(0.0, 0, total)

            self.plan.output_dir.mkdir(parents=True, exist_ok=True)
            extract_archive(
                str(self.plan.archive),
                str(self.plan.output_dir),
                self.plan.files or [],
            )

            if progress:
                progress(1.0, total, total)

            return JobResult(ok=True, error=None, plan=self.plan)

        except BaseException as e:
            return JobResult(ok=False, error=e, plan=self.plan)

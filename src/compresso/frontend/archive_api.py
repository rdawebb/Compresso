"""Archive API for multi-file operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .._core import create_archive, extract_archive, list_archive_contents


@dataclass
class ArchiveEntry:
    """Represents a file or directory in an archive."""

    path: str
    size: int
    is_dir: bool
    is_symlink: bool
    mtime: float
    mode: int
    link_target: str | None = None


@dataclass
class ArchiveOptions:
    """Options for creating archives."""

    format: str = "tar.zst"  # Default to tar with zstd
    compression_level: int | None = None
    preserve_permissions: bool = True
    preserve_timestamps: bool = True
    exclude_patterns: list[str] | None = None


class ArchiveJob:
    """Job for creating an archive."""

    def __init__(
        self, sources: list[Path], output: Path, options: ArchiveOptions
    ) -> None:
        """Initialise archive job.

        Args:
            sources: List of source paths to archive.
            output: Path to the output archive file.
            options: Archive options.
        """
        self.sources = [Path(s) for s in sources]
        self.output = Path(output)
        self.options = options

    @classmethod
    def from_paths(
        cls, sources: list[Path], output: Path, format: str = "tar.zst"
    ) -> ArchiveJob:
        """Create archive job from file paths.

        Args:
            sources: List of source paths to archive.
            output: Path to the output archive file.
            format: Archive format (default: "tar.zst").

        Returns:
            ArchiveJob instance.
        """
        options = ArchiveOptions(format=format)

        return cls(sources, output, options)

    def run(self, progress: Callable | None = None) -> None:
        """Create the archive.

        Args:
            progress: Optional callback for progress updates.
        """
        # Convert Python paths to C-compatible list
        source_paths = [str(s) for s in self.sources]

        # Call C extension
        create_archive(
            str(self.output),
            self.options.format,
            source_paths,
            self.options.compression_level or -1,
        )


class ExtractJob:
    """Job for extracting an archive."""

    def __init__(
        self, archive: Path, output_dir: Path, files: list[str] | None = None
    ) -> None:
        """Initialise extract job.

        Args:
            archive: Path to the archive file.
            output_dir: Directory to extract to.
            files: Optional list of files to extract. If None, extracts all files.
        """
        self.archive = Path(archive)
        self.output_dir = Path(output_dir)
        self.files = files  # None = extract all

    @classmethod
    def from_archive(cls, archive: Path, output_dir: Path | None = None) -> ExtractJob:
        """Create extract job from archive path.

        Args:
            archive: Path to the archive file.
            output_dir: Directory to extract to. If None, extracts to current directory.

        Returns:
            ExtractJob instance.
        """
        if output_dir is None:
            output_dir = Path.cwd()

        return cls(archive, output_dir)

    def list_contents(self) -> list[str]:
        """List archive entry paths without extracting.

        Returns:
            List of entry paths.
        """
        return list(list_archive_contents(str(self.archive)))

    def run(self, progress: Callable | None = None) -> None:
        """Extract the archive.

        Args:
            progress: Optional callback for progress updates.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        extract_archive(str(self.archive), str(self.output_dir), self.files or [])

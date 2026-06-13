"""Archive API for multi-file operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import compresso._core as core


@dataclass
class ArchiveEntry:
    """Represents a file or directory in an archive."""

    path: str
    size: int
    is_dir: bool
    is_symlink: bool
    mtime: float
    mode: int
    link_target: Optional[str] = None


@dataclass
class ArchiveOptions:
    """Options for creating archives."""

    format: str = "tar.zst"  # Default to tar with zstd
    compression_level: Optional[int] = None
    preserve_permissions: bool = True
    preserve_timestamps: bool = True
    exclude_patterns: Optional[List[str]] = None


class ArchiveJob:
    """Job for creating an archive."""

    def __init__(self, sources: List[Path], output: Path, options: ArchiveOptions):
        self.sources = [Path(s) for s in sources]
        self.output = Path(output)
        self.options = options

    @classmethod
    def from_paths(
        cls, sources: List[Path], output: Path, format: str = "tar.zst"
    ) -> "ArchiveJob":
        """Create archive job from file paths."""
        options = ArchiveOptions(format=format)
        return cls(sources, output, options)

    def run(self, progress: Optional[Callable] = None):
        """Create the archive."""
        # Convert Python paths to C-compatible list
        source_paths = [str(s) for s in self.sources]

        # Call C extension
        core.create_archive(
            str(self.output),
            self.options.format,
            source_paths,
            self.options.compression_level or -1,
        )


class ExtractJob:
    """Job for extracting an archive."""

    def __init__(
        self, archive: Path, output_dir: Path, files: Optional[List[str]] = None
    ):
        self.archive = Path(archive)
        self.output_dir = Path(output_dir)
        self.files = files  # None = extract all

    @classmethod
    def from_archive(
        cls, archive: Path, output_dir: Optional[Path] = None
    ) -> "ExtractJob":
        """Create extract job from archive path."""
        if output_dir is None:
            output_dir = Path.cwd()
        return cls(archive, output_dir)

    def list_contents(self) -> List[ArchiveEntry]:
        """List archive contents without extracting."""
        entries_raw = core.list_archive_contents(str(self.archive))

        entries = []
        for e in entries_raw:
            entries.append(
                ArchiveEntry(
                    path=e["path"],
                    size=e["size"],
                    is_dir=e["is_dir"],
                    is_symlink=e["is_symlink"],
                    mtime=e["mtime"],
                    mode=e["mode"],
                    link_target=e.get("link_target"),
                )
            )

        return entries

    def run(self, progress: Optional[Callable] = None):
        """Extract the archive."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        core.extract_archive(str(self.archive), str(self.output_dir), self.files or [])

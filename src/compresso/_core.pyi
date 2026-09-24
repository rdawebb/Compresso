"""Type stubs for the _core C extension module."""

from collections.abc import Callable
from typing import Literal, NotRequired, TypeAlias, TypedDict

class Error(Exception):
    """Base error for compression operations."""

class HeaderError(Error):
    """Error reading or writing compression headers."""

class BackendError(Error):
    """Error in compression backend."""

class Cancelled(Error):
    """Raised when an operation stopped because its CancelToken was set."""

class CancelToken:
    """Cancellation flag shared with a running compression.

    Pass one as the `cancel=` argument of any operation below; calling
    `cancel()` stops it within one 64 KB chunk, raising `Cancelled` and leaving no
    partial destination file behind.
    """

    def cancel(self) -> None:
        """Request cancellation. Safe to call from any thread."""

    @property
    def cancelled(self) -> bool:
        """True once cancel() has been called."""

# Progress callbacks receive (done_bytes, total_bytes), counting input bytes
# consumed; returning is enough to continue; raising aborts the operation with
# that exception
ProgressFn: TypeAlias = Callable[[int, int], None]

def compress_file(
    src_path: str,
    dst_path: str,
    algo: str,
    strategy: str,
    level: int,
    *,
    overwrite: int = ...,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> str:
    """Compress a file using the specified algorithm and strategy.

    `overwrite` is 0 = error (default), 1 = skip, 2 = overwrite, 3 = rename;
    applied to the destination file.

    Returns the path actually written, which RENAME may have changed from
    `dst_path`; SKIP returns `dst_path` unchanged and writes nothing.
    """

def decompress_file(
    src_path: str,
    dst_path: str,
    algo: str,
    *,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> int:
    """Decompress a file."""

# Stub-only: the extension builds a plain dict per backend, with every key set
class _CapabilityDict(TypedDict):
    name: str
    id: int

def get_capabilities() -> list[_CapabilityDict | None]:
    """Get list of available compression backends.

    A slot is None when its backend is not registered.
    """

class _ArchiveCapabilityDict(TypedDict):
    name: str
    streaming: bool
    compression: bool

def archive_capabilities() -> list[_ArchiveCapabilityDict]:
    """Get the capabilities of available archive backends.

    Only backends that are available are listed.
    """

def get_default_backend_for_strategy(strategy: str) -> str:
    """Get the default backend for the given strategy."""

def create_archive(
    output_path: str,
    format: str,
    input_paths: list[str],
    compression_level: int = ...,
    *,
    overwrite: int = ...,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> str:
    """Create an archive from the given input paths in the given format.

    `overwrite` is 0 = error (default), 1 = skip, 2 = overwrite, 3 = rename;
    applied to the single destination archive.

    Returns the path actually written, which RENAME may have changed from
    `output_path`; SKIP returns `output_path` unchanged and writes nothing.
    """

def extract_archive(
    archive_path: str,
    output_dir: str,
    files: list[str] = ...,
    *,
    overwrite: int = ...,
    max_total_size: int = ...,
    max_depth: int = ...,
    preserve_permissions: bool = ...,
    preserve_timestamps: bool = ...,
    allow_symlinks: int = ...,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> None:
    """Extract an archive to output_dir, optionally selecting specific files.

    The keyword-only arguments are the extraction policy; each defaults to the
    value in `extraction_policy_default()` in the C extension.

    `overwrite` is 0 = error, 1 = skip, 2 = overwrite, 3 = rename;
    `allow_symlinks` is 0 = deny, 1 = allow, 2 = rewrite to regular files;
    `max_total_size` and `max_depth` treat 0 as unlimited.
    """

def detect_format(file_path: str) -> str:
    """Name a file's format, from its magic bytes and then its extension."""

def format_is_archive(format: str) -> bool:
    """Return whether a format's container can hold more than one entry."""

EntryTypeName: TypeAlias = Literal["file", "dir", "symlink", "special"]

class ArchiveEntryDict(TypedDict):
    """One archive entry, as `list_archive_contents` reports it.

    `compressed_size`, `crc` and `method` are present only for a container that
    compresses each entry separately. tar compresses the whole stream at once,
    so those keys are absent rather than None.
    """

    path: str
    type: EntryTypeName
    # Uncompressed size as the archive declares it (0 for directories); not
    # trusted for extraction limits, which count the bytes actually written
    size: int
    mtime: int
    mode: int
    # The stored target of a symlink, and None for every other type
    link_target: str | None
    compressed_size: NotRequired[int]
    crc: NotRequired[int]
    method: NotRequired[int]

def list_archive_contents(archive_path: str) -> list[ArchiveEntryDict]:
    """Describe each entry in an archive."""

def compress_standalone(
    input_path: str,
    output_path: str,
    format: str,
    compression_level: int = ...,
    *,
    overwrite: int = ...,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> str:
    """Compress a file into a standalone container (.gz, .bz2, .xz, .zst, .lz4).

    `overwrite` is 0 = error (default), 1 = skip, 2 = overwrite, 3 = rename;
    applied to the destination file.

    Returns the path actually written, which RENAME may have changed from
    `output_path`; SKIP returns `output_path` unchanged and writes nothing.
    """

def decompress_standalone(
    input_path: str,
    output_path: str,
    format: str = ...,
    *,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> None:
    """Decompress a standalone container, detecting the format if not given."""

"""Type stubs for the _core C extension module."""

from collections.abc import Callable
from typing import TypeAlias

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
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> int:
    """Compress a file using the specified algorithm and strategy."""

def decompress_file(
    src_path: str,
    dst_path: str,
    algo: str,
    *,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> int:
    """Decompress a file."""

def get_capabilities() -> list[tuple[str, int, bool, bool]]:
    """Get list of available compression backends."""

def get_default_backend_for_strategy(strategy: str) -> str:
    """Get the default backend for the given strategy."""

def create_archive(
    output_path: str,
    format: str,
    input_paths: list[str],
    compression_level: int = ...,
) -> None:
    """Create an archive from the given input paths in the given format."""

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
) -> None:
    """Extract an archive to output_dir, optionally selecting specific files.

    The keyword-only arguments are the extraction policy; each defaults to the
    value in `extraction_policy_default()` in the C extension.

    `overwrite` is 0 = error, 1 = skip, 2 = overwrite; `allow_symlinks` is 0 = deny,
    1 = allow, 2 = rewrite to regular files; `max_total_size` and `max_depth`
    treat 0 as unlimited.
    """

def list_archive_contents(archive_path: str) -> list[str]:
    """List the entry paths contained in an archive."""

def compress_standalone(
    input_path: str,
    output_path: str,
    format: str,
    compression_level: int = ...,
    *,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> None:
    """Compress a file into a standalone container (.gz, .bz2, .xz, .zst, .lz4)."""

def decompress_standalone(
    input_path: str,
    output_path: str,
    format: str = ...,
    *,
    progress: ProgressFn | None = ...,
    cancel: CancelToken | None = ...,
) -> None:
    """Decompress a standalone container, detecting the format if not given."""

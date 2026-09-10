"""Type stubs for the _core C extension module."""

class Error(Exception):
    """Base error for compression operations."""

class HeaderError(Error):
    """Error reading or writing compression headers."""

class BackendError(Error):
    """Error in compression backend."""

def compress_file(
    src_path: str,
    dst_path: str,
    algo: str,
    strategy: str,
    level: int,
) -> int:
    """Compress a file using the specified algorithm and strategy."""

def decompress_file(
    src_path: str,
    dst_path: str,
    algo: str,
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

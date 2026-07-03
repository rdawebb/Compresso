"""Type stubs for the _core C extension module."""

class Error(Exception):
    """Base error for compression operations."""

    pass

class HeaderError(Error):
    """Error reading or writing compression headers."""

    pass

class BackendError(Error):
    """Error in compression backend."""

    pass

def compress_file(
    src_path: str,
    dst_path: str,
    algo: str,
    strategy: str,
    level: int,
) -> int:
    """Compress a file using the specified algorithm and strategy."""
    ...

def decompress_file(
    src_path: str,
    dst_path: str,
    algo: str,
) -> int:
    """Decompress a file."""
    ...

def get_capabilities() -> list[tuple[str, int, bool, bool]]:
    """Get list of available compression backends."""
    ...

def get_default_backend_for_strategy(strategy: str) -> str:
    """Get the default backend for the given strategy."""
    ...

def create_archive(
    output_path: str,
    format: str,
    input_paths: list[str],
    compression_level: int = ...,
) -> None:
    """Create an archive from the given input paths in the given format."""
    ...

def extract_archive(
    archive_path: str,
    output_dir: str,
    files: list[str] = ...,
) -> int:
    """Extract an archive to output_dir, optionally selecting specific files."""
    ...

def list_archive_contents(archive_path: str) -> list[str]:
    """List the entry paths contained in an archive."""
    ...

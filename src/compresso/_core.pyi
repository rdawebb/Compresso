"""Type stubs for the _core C extension module."""

from typing import List, Tuple

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
    input_path: str,
    output_path: str,
    algorithm: str,
    strategy: str,
    level: int,
) -> int:
    """Compress a file using the specified algorithm and strategy."""
    ...

def decompress_file(
    input_path: str,
    output_path: str,
    algorithm: str,
) -> int:
    """Decompress a file."""
    ...

def get_capabilities() -> List[Tuple[str, int, bool, bool]]:
    """Get list of available compression backends."""
    ...

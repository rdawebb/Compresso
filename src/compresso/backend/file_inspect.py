"""Module for inspecting Compresso compressed files and extracting metadata"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .capabilities import get_by_id
from .speeds import get_estimated_speeds

COMP_HEADER_STRUCT = struct.Struct(
    "<4sBBBBQ"
)  # magic, version, algo, level, flags, original_size


@dataclass
class InspectResult:
    """Holds the result of inspecting a compressed file

    Attributes:
        path (Path): The path to the compressed file.
        is_compresso (bool): Whether the file is a valid Compresso file.
        header_ok (bool): Whether the file header is valid.
        reason (Optional[str]): Reason for invalidity if applicable.
        version (Optional[int]): Compresso version.
        algo_id (Optional[int]): Algorithm ID.
        algo_name (Optional[str]): Algorithm name.
        level (Optional[int]): Compression level (None if auto/unspecified).
        flags (Optional[int]): Compression flags.
        orig_size (Optional[int]): Original uncompressed size in bytes.
        backend_available (bool): Whether the backend for the algorithm is available.
        has_streaming (bool): Whether the algorithm supports streaming.
        can_decompress (bool): Whether the file can be decompressed with the available backends.
        estimated_decomp_s (Optional[float]): Estimated decompression time in seconds.
    """

    path: Path

    # File recognition
    is_compresso: bool
    header_ok: bool
    reason: Optional[str]

    # Header fields (if is_compresso)
    version: Optional[int]
    algo_id: Optional[int]
    algo_name: Optional[str]
    level: Optional[int]  # None is 'auto' or 'unspecified'
    flags: Optional[int]
    orig_size: Optional[int]  # Original uncompressed bytes

    # Backend info
    backend_available: bool
    has_streaming: bool

    # UI helpers
    can_decompress: bool
    estimated_decomp_s: Optional[float]  # in seconds


def _failed_inspection(
    path: Path, reason: str, is_compresso: bool = False
) -> InspectResult:
    """Helper function to create a failed inspection result.

    Args:
        path (Path): The path to the file.
        reason (str): The reason for the failure.
        is_compresso (bool): Whether the file is a Compresso file.

    Returns:
        InspectResult: The failed inspection result.
    """
    return InspectResult(
        path=path,
        is_compresso=is_compresso,
        header_ok=False,
        reason=reason,
        version=None,
        algo_id=None,
        algo_name=None,
        level=None,
        flags=None,
        orig_size=None,
        backend_available=False,
        has_streaming=False,
        can_decompress=False,
        estimated_decomp_s=None,
    )


def inspect(path: str | Path) -> InspectResult:
    """Inspect a compressed file and extract metadata

    Args:
        path (str | Path): The path to the compressed file.

    Returns:
        InspectResult: The result of the inspection.
    """
    path = Path(path)
    if not path.is_file():
        return _failed_inspection(path, "Not a file")

    try:
        with path.open("rb") as f:
            data = f.read(COMP_HEADER_STRUCT.size)
    except OSError as e:
        return _failed_inspection(path, f"Failed to read file: {e}")

    if len(data) < COMP_HEADER_STRUCT.size:
        return _failed_inspection(path, "File too small to be a valid Compresso file")

    magic, version, algo_id, level, flags, orig_size = COMP_HEADER_STRUCT.unpack(data)

    if magic != b"COMP":
        return _failed_inspection(path, "Invalid magic number", is_compresso=False)

    if version != 1:
        return _failed_inspection(path, f"Unsupported header version: {version}")

    cap = get_by_id(algo_id)
    backend_available = cap is not None and cap.is_available()
    algo_name = cap.name if cap else None
    has_streaming = cap.has_stream if cap else False

    can_decompress = backend_available
    reason = None
    if not can_decompress:
        reason = "No available backend for this algorithm"

    est_time = None
    if can_decompress and orig_size > 0:
        if algo_name is not None:
            mb_s = get_estimated_speeds(algo_name, operation="decompress")
        else:
            mb_s = 200.0
        est_time = orig_size / (mb_s * 1024 * 1024)

    return InspectResult(
        path=path,
        is_compresso=True,
        header_ok=True,
        reason=reason,
        version=version,
        algo_id=algo_id,
        algo_name=algo_name,
        level=level if level != 255 else None,
        flags=flags,
        orig_size=orig_size,
        backend_available=backend_available,
        has_streaming=has_streaming,
        can_decompress=can_decompress,
        estimated_decomp_s=est_time,
    )

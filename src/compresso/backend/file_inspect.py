"""Module for inspecting Compresso compressed files and extracting metadata"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .capabilities import get_by_id
from .speeds import get_estimated_speeds

COMP_HEADER_STRUCT = struct.Struct(
    "<4sBBBBQ"
)  # magic, version, algo, level, flags, original_size

_LEVEL_AUTO = 255  # Special value indicating 'auto' or 'unspecified' level


@dataclass
class InspectResult:
    """Holds the result of inspecting a compressed file

    Attributes:
        path: The path to the compressed file.
        is_compresso: Whether the file is a valid Compresso file.
        header_ok: Whether the file header is valid.
        reason: Reason for invalidity if applicable.
        version: Compresso version.
        algo_id: Algorithm ID.
        algo_name: Human-readable algorithm name for user display.
        level: Compression level (None if auto/unspecified).
        flags: Compression flags.
        orig_size: Original uncompressed size in bytes.
        backend_available: Whether the backend for the algorithm is available.
        has_streaming: Whether the algorithm supports streaming.
        can_decompress: Whether the file can be decompressed with the available backends.
        estimated_decomp_s: Estimated decompression time in seconds.
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
        path: The path to the file.
        reason: The reason for the failure.
        is_compresso: Whether the file is a Compresso file.

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


def inspect(path: Union[str, Path]) -> InspectResult:
    """Inspect a compressed file and extract metadata

    Args:
        path: The path to the compressed file.

    Returns:
        InspectResult: The result of the inspection.
    """
    path = Path(path)
    if not path.is_file():
        return _failed_inspection(path, reason="Not a file")

    try:
        with path.open(mode="rb") as f:
            data: bytes = f.read(COMP_HEADER_STRUCT.size)
    except OSError as e:
        return _failed_inspection(path, reason=f"Failed to read file: {e}")

    if len(data) < COMP_HEADER_STRUCT.size:
        return _failed_inspection(
            path, reason="File too small to be a valid Compresso file"
        )

    magic, version, algo_id, level, flags, orig_size = COMP_HEADER_STRUCT.unpack(data)

    if magic != b"COMP":
        return _failed_inspection(
            path, reason="Invalid magic number", is_compresso=False
        )

    if version != 1:
        return _failed_inspection(path, reason=f"Unsupported header version: {version}")

    _MAX_ORIG_SIZE = (1 << 63) - 1

    if orig_size > _MAX_ORIG_SIZE:
        return _failed_inspection(
            path,
            reason=f"Original size {orig_size} exceeds maximum limit {_MAX_ORIG_SIZE}",
            is_compresso=True,
        )

    cap = get_by_id(cid=algo_id)
    backend_available: bool = cap is not None and cap.is_available()
    algo_name: str | None = cap.name if cap else None
    has_streaming: bool = cap.has_stream if cap else False

    can_decompress: bool = backend_available
    reason: Optional[str] = None
    if not can_decompress:
        reason = "No available backend for this algorithm"

    est_time = None
    if can_decompress and orig_size > 0:
        if algo_name is not None:
            mb_s: int | float = get_estimated_speeds(
                algo=algo_name, operation="decompress"
            )
        else:
            mb_s = 200.0
        est_time: float | None = orig_size / (mb_s * 1024 * 1024)

    return InspectResult(
        path=path,
        is_compresso=True,
        header_ok=True,
        reason=reason,
        version=version,
        algo_id=algo_id,
        algo_name=algo_name,
        level=level if level != _LEVEL_AUTO else None,
        flags=flags,
        orig_size=orig_size,
        backend_available=backend_available,
        has_streaming=has_streaming,
        can_decompress=can_decompress,
        estimated_decomp_s=est_time,
    )

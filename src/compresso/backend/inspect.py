"""Module for inspecting Compresso compressed files and extracting metadata."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .capabilities import get_by_id


COMP_HEADER_STRUCT = struct.Struct("<4sBBBQ") # magic, version, algo, level, flags, original_size

# Estimated decompress speeds (placeholder)
_EST_MB_S = {
    "zlib": 200.0,
    "bzip2": 50.0,
    "lzma": 30.0,
    "zstd": 400.0,
    "lz4": 800.0,
    "snappy": 600.0,
}


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
    level: Optional[int]      # None is 'auto' or 'unspecified'
    flags: Optional[int]
    orig_size: Optional[int]  # Original uncompressed bytes

    # Backend info
    backend_available: bool
    has_streaming: bool

    # UI helpers
    can_decompress: bool
    estimated_decomp_s: Optional[float]  # in seconds

def inspect(path: str | Path) -> InspectResult:
    """Inspect a compressed file and extract metadata

    Args:
        path (str | Path): The path to the compressed file.

    Returns:
        InspectResult: The result of the inspection.
    """
    path = Path(path)
    if not path.is_file():
        return InspectResult(
            path=path,
            is_compresso=False,
            header_ok=False,
            reason="Not a file",
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
    
    try:
        with path.open("rb") as f:
            data = f.read(COMP_HEADER_STRUCT.size)
    except OSError as e:
        return InspectResult(
            path=path,
            is_compresso=False,
            header_ok=False,
            reason=f"Failed to read file: {e}",
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
    
    if len(data) < COMP_HEADER_STRUCT.size:
        return InspectResult(
            path=path,
            is_compresso=False,
            header_ok=False,
            reason="File too small to be a valid Compresso file",
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
    
    magic, version, algo_id, level, flags, orig_size = COMP_HEADER_STRUCT.unpack(data)

    if magic != b'COMP':
        return InspectResult(
            path=path,
            is_compresso=False,
            header_ok=False,
            reason="Magic bytes do not match Compresso format",
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
    
    if version != 1:
        return InspectResult(
            path=path,
            is_compresso=True,
            header_ok=False,
            reason=f"Unsupported header version: {version}",
            version=version,
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
    
    cap = get_by_id(algo_id)
    backend_available = cap is not None and cap.is_available()
    algo_name = cap.name if cap else None
    has_streaming = cap.has_streaming if cap else False

    can_decompress = backend_available
    reason = None
    if not can_decompress:
        reason = "No available backend for this algorithm"

    est_time = None
    if can_decompress and orig_size > 0:
        if algo_name is not None:
            mb_s = _EST_MB_S.get(algo_name, 200.0)
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
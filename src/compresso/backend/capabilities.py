"""Module for querying compression backend capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from compressor import _core


@dataclass(frozen=True)
class BackendCapabilities:
    """Holds the capability information for a compression backend

    Attributes:
        name (str): Name of the compression algorithm.
        id (int): Algorithm ID.
        has_buffer (bool): Whether the backend has a buffer.
        has_stream (bool): Whether the backend supports streaming compression/decompression.
    """
    name: str
    id: int
    has_buffer: bool
    has_stream: bool

## Cached capabilities
_cap_list: List[BackendCapabilities] | None = None
_cap_by_name: Dict[str, BackendCapabilities] | None = None
_cap_by_id: Dict[int, BackendCapabilities] | None = None

def _load_capabilities() -> None:
    """Load capabilities from the compressor module"""
    global _cap_list, _cap_by_name, _cap_by_id
    raw = _core.get_capabilities()
    caps: List[BackendCapabilities] = []
    by_name: Dict[str, BackendCapabilities] = {}
    by_id: Dict[int, BackendCapabilities] = {}

    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        cid = int(item.get("id", -1))
        has_buffer = bool(item.get("has_buffer", False))
        has_stream = bool(item.get("has_stream", False))

        cap = BackendCapabilities(
            name=name,
            id=cid,
            has_buffer=has_buffer,
            has_stream=has_stream,
        )

        caps.append(cap)
        by_name[name] = cap
        by_id[cid] = cap

    _cap_list = caps
    _cap_by_name = by_name
    _cap_by_id = by_id

def list_capabilities() -> List[BackendCapabilities]:
    """List capabilities of all compiled compression backends

    Returns:
        List of BackendCapabilities objects
    """
    if _cap_list is None:
        _load_capabilities()
    return _cap_list  # type: ignore[return-value]

def get_by_name(name: str) -> BackendCapabilities | None:
    """Get capability information by algorithm name

    Args:
        name (str): Name of the compression algorithm.

    Returns:
        BackendCapabilities or None if not found
    """
    if _cap_by_name is None:
        _load_capabilities()
    return _cap_by_name.get(name)  # type: ignore[attr-defined]

def get_by_id(cid: int) -> BackendCapabilities | None:
    """Get capability information by algorithm ID

    Args:
        cid (int): Algorithm ID.

    Returns:
        BackendCapabilities or None if not found
    """
    if _cap_by_id is None:
        _load_capabilities()
    return _cap_by_id.get(cid)  # type: ignore[attr-defined]
    
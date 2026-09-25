"""Module for querying compression backend capabilities"""

from __future__ import annotations

from dataclasses import dataclass

from .. import _core


@dataclass(frozen=True)
class BackendCapabilities:
    """Holds the capability information for a compression backend

    Attributes:
        name: Name of the compression algorithm.
        id: Algorithm ID.
        min_level: Lowest compression level accepted, or None if the
            algorithm has no levels (only the default is accepted).
        max_level: Highest compression level accepted, or None likewise.
    """

    name: str
    id: int
    min_level: int | None
    max_level: int | None

    def is_available(self) -> bool:
        """Check if the backend is available for use

        Returns:
            bool: Always True if the backend is compiled and listed.
        """
        return True

    def accepts_level(self, level: int | None) -> bool:
        """Check whether the backend accepts a compression level

        Args:
            level: The level to check, or None for the backend's default.

        Returns:
            bool: True if the core would accept `level` for this backend.
        """
        if level is None:
            return True

        if self.min_level is None or self.max_level is None:
            return False

        return self.min_level <= level <= self.max_level


## Cached capabilities
_cap_list: list[BackendCapabilities] | None = None
_cap_by_name: dict[str, BackendCapabilities] | None = None
_cap_by_id: dict[int, BackendCapabilities] | None = None


def _load_capabilities() -> None:
    """Load capabilities from the compressor module"""
    global _cap_list, _cap_by_name, _cap_by_id
    caps: list[BackendCapabilities] = []
    by_name: dict[str, BackendCapabilities] = {}
    by_id: dict[int, BackendCapabilities] = {}

    for item in _core.get_capabilities():
        # Unregistered backend slots come back as None
        if item is None:
            continue

        cap = BackendCapabilities(
            name=item["name"],
            id=item["id"],
            min_level=item["min_level"],
            max_level=item["max_level"],
        )

        caps.append(cap)
        by_name[cap.name] = cap
        by_id[cap.id] = cap

    _cap_list = caps
    _cap_by_name = by_name
    _cap_by_id = by_id


def list_capabilities() -> list[BackendCapabilities]:
    """List capabilities of all compiled compression backends

    Returns:
        List of BackendCapabilities objects
    """
    if _cap_list is None:
        _load_capabilities()

    return _cap_list  # type: ignore[return-value]  # ty:ignore[invalid-return-type]


def get_by_name(name: str) -> BackendCapabilities | None:
    """Get capability information by algorithm name

    Args:
        name: Name of the compression algorithm.

    Returns:
        BackendCapabilities or None if not found
    """
    if _cap_by_name is None:
        _load_capabilities()

    return _cap_by_name.get(name)  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]


def get_by_id(cid: int) -> BackendCapabilities | None:
    """Get capability information by algorithm ID

    Args:
        cid: Algorithm ID.

    Returns:
        BackendCapabilities or None if not found
    """
    if _cap_by_id is None:
        _load_capabilities()

    return _cap_by_id.get(cid)  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

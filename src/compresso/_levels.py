"""The compression-level sentinel shared by every call into the core."""

from __future__ import annotations

# The core's default level flag; None in the Python API
LEVEL_AUTO = -1


def to_core_level(level: int | None) -> int:
    """Translate an API level into the core's convention.

    Args:
        level: A compression level, or None for the backend's default.

    Returns:
        `level` unchanged, or LEVEL_AUTO for None; 0 is a real level and is
        passed through.
    """
    return LEVEL_AUTO if level is None else int(level)

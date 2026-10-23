"""What `formats` prints: the algorithms and formats this build can write."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...introspect.capabilities import BackendCapabilities
from .._app import app

if TYPE_CHECKING:
    from ..._core import _StandaloneFormatDict


def _heading(text: str, count: int) -> None:
    app.echo(message=f"{text}: {count}\n")


def _item(name: str, details: dict[str, str]) -> None:
    app.echo(message=app.style(text=f"- {name}", fg="green", bold=True))
    width = max(map(len, details), default=0) + 2
    for label, value in details.items():
        app.echo(message=f"  {label + ':':<{width}}{value}")

    app.echo()


def list_formats(
    algos: list[BackendCapabilities],
    standalone: list[_StandaloneFormatDict],
    archives: dict[str, list[str]],
) -> None:
    """Print the .comp algorithms, then the standalone and archive formats.

    Args:
        algos: The backends a .comp container can use.
        standalone: The single-file container formats.
        archives: Each archive format, mapped to the compressed variants
            written as `<archive>.<codec>`, e.g. `tar.zst`.
    """
    _heading("Available compression algorithms", len(algos))
    for cap in algos:
        levels = "none" if cap.min_level is None else f"{cap.min_level}-{cap.max_level}"
        _item(cap.name, {"ID": str(cap.id), "Levels": levels})

    _heading("Standalone formats", len(standalone))
    for fmt in standalone:
        _item(
            fmt["name"],
            {
                "Aliases": ", ".join(fmt["aliases"]) or "none",
                "Extensions": ", ".join(f".{ext}" for ext in fmt["exts"]),
            },
        )

    _heading("Archive formats", len(archives))
    for name, compressed in archives.items():
        _item(name, {"Compressed": ", ".join(compressed)} if compressed else {})

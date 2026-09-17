"""The ExtendedTyper application every command registers against."""

from __future__ import annotations

from typer_extensions import ExtendedTyper

app = ExtendedTyper(help="Compresso - Fast file compression and decompression tool")

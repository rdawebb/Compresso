"""The `inspect` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from ..backend.file_inspect import inspect as inspect_file
from ._app import app
from ._render import (
    EXIT_USAGE,
    cancelled,
    fail,
    format_size,
    format_time,
    succeed,
)


def inspect(
    file: Annotated[Path, app.Argument(help="File to inspect")],
    output_json: Annotated[
        bool, app.Option("--json", help="Output in JSON format")
    ] = False,
) -> None:
    """Inspect a compressed file and show metadata.

    Args:
        file: The path to the compressed file.
        output_json: If True, output metadata in JSON format.
    """
    try:
        result = inspect_file(path=file)

        if output_json:
            data: dict[str, str | int | None | float] = {
                "path": str(object=result.path),
                "is_compresso": result.is_compresso,
                "header_ok": result.header_ok,
                "version": result.version,
                "algo_id": result.algo_id,
                "algo_name": result.algo_name,
                "level": result.level,
                "flags": result.flags,
                "orig_size": result.orig_size,
                "backend_available": result.backend_available,
                "has_streaming": result.has_streaming,
                "can_decompress": result.can_decompress,
                "estimated_decomp_s": result.estimated_decomp_s,
                "reason": result.reason,
            }
            app.echo(message=json.dumps(obj=data, indent=2))
            return

        app.echo(message=f"File: {result.path}")
        app.echo()

        if not result.is_compresso:
            reason = f"\n  Reason: {result.reason}" if result.reason else ""
            fail(f"Not a valid Compresso file{reason}", EXIT_USAGE)

        if not result.header_ok:
            reason = f"\n  Reason: {result.reason}" if result.reason else ""
            fail(f"Invalid file header{reason}", EXIT_USAGE)

        succeed("Valid Compresso file")
        app.echo()
        app.echo(
            message=f"Algorithm:       {result.algo_name or 'Unknown'} (ID: {result.algo_id})"
        )
        app.echo(message=f"Version:         {result.version}")
        app.echo()

        if result.level is not None:
            app.echo(message=f"Level:           {result.level}")

        else:
            app.echo(message="Level:           auto")

        if result.orig_size:
            app.echo(
                message=f"Original size:   {format_size(size_bytes=result.orig_size)}"
            )

        compressed_size: int = file.stat().st_size
        app.echo(message=f"Compressed size: {format_size(size_bytes=compressed_size)}")

        if result.orig_size:
            ratio: int | float = (compressed_size / result.orig_size) * 100
            app.echo(message=f"Compression:     {ratio:.1f}% of original")

        app.echo()
        app.echo(
            message=f"Backend available:  {'Yes' if result.backend_available else 'No'}"
        )
        app.echo(
            message=f"Streaming support:  {'Yes' if result.has_streaming else 'No'}"
        )
        app.echo(
            message=f"Can decompress:     {'Yes' if result.can_decompress else 'No'}"
        )

        if result.estimated_decomp_s:
            app.echo(
                message=f"Est. decomp time:   {format_time(seconds=result.estimated_decomp_s)}\n"
            )

        if not result.can_decompress and result.reason:
            app.echo()
            app.echo(message=app.style(text=f"⚠ {result.reason}", fg="yellow"))

    except KeyboardInterrupt:
        cancelled("Inspection")

    except Exception as e:
        fail(f"Error inspecting file: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["i", "info"])(inspect)

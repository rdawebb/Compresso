"""The `inspect` command."""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from typing import Annotated

from .._core import detect_format
from ..backend.file_inspect import inspect as inspect_file
from ..frontend.archive_api import ArchiveEntry
from ..frontend.archive_api import plan_extraction as plan_archive_extraction
from ._app import app
from ._dispatch import looks_like_archive
from ._render import (
    EXIT_USAGE,
    cancelled,
    fail,
    format_size,
    format_time,
    succeed,
)


def _entry_detail_line(entry: ArchiveEntry) -> str:
    """Format one entry's full detail: mode, mtime, size, path, and target.

    Args:
        entry: The archive entry to format.

    Returns:
        A single formatted line for the entry.
    """
    if entry.is_dir:
        type_bit = stat.S_IFDIR
    elif entry.is_symlink:
        type_bit = stat.S_IFLNK
    else:
        type_bit = stat.S_IFREG

    mode = stat.filemode(type_bit | entry.mode)
    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.mtime))
    size = (
        "" if entry.is_dir or entry.is_symlink else format_size(size_bytes=entry.size)
    )
    target = f" -> {entry.link_target}" if entry.is_symlink else ""

    detail = ""
    if entry.compressed_size is not None:
        detail = f"  (compressed {format_size(size_bytes=entry.compressed_size)}"
        if entry.crc is not None:
            detail += f", crc {entry.crc:08x}"
        if entry.method is not None:
            detail += f", method {entry.method}"
        detail += ")"

    return f"{mode}  {mtime}  {size:>10}  {entry.path}{target}{detail}"


def _inspect_archive(file: Path, output_json: bool, show_entries: bool) -> None:
    """Inspect an archive: a container that can hold many entries.

    Args:
        file: The path to the archive.
        output_json: If True, output metadata in JSON format.
        show_entries: If True, also report each entry's own detail.
    """
    plan = plan_archive_extraction(archive=file)

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    fmt = detect_format(str(file))
    total_size = sum(entry.size for entry in plan.entries)
    archive_size: int = file.stat().st_size

    if output_json:
        data: dict[str, object] = {
            "path": str(file),
            "format": fmt,
            "is_archive": True,
            "entry_count": len(plan.entries),
            "total_size": total_size,
            "archive_size": archive_size,
        }
        if show_entries:
            data["entries"] = [
                {
                    "path": entry.path,
                    "size": entry.size,
                    "is_dir": entry.is_dir,
                    "is_symlink": entry.is_symlink,
                    "mtime": entry.mtime,
                    "mode": entry.mode,
                    "link_target": entry.link_target,
                    "compressed_size": entry.compressed_size,
                    "crc": entry.crc,
                    "method": entry.method,
                }
                for entry in plan.entries
            ]
        app.echo(message=json.dumps(obj=data, indent=2))
        return

    app.echo(message=f"File:          {file}")
    app.echo(message=f"Format:        {fmt}")
    app.echo(message=f"Entries:       {len(plan.entries)}")
    app.echo(message=f"Total size:    {format_size(size_bytes=total_size)}")
    app.echo(message=f"Archive size:  {format_size(size_bytes=archive_size)}")

    if total_size:
        ratio: int | float = (archive_size / total_size) * 100
        app.echo(message=f"Compression:   {ratio:.1f}% of original")

    if show_entries:
        app.echo()
        for entry in plan.entries:
            app.echo(message=_entry_detail_line(entry))


def inspect(
    file: Annotated[Path, app.Argument(help="File to inspect")],
    output_json: Annotated[
        bool, app.Option("--json", help="Output in JSON format")
    ] = False,
    show_entries: Annotated[
        bool,
        app.Option(
            "--entries", "-e", help="For an archive, also list each entry's detail"
        ),
    ] = False,
) -> None:
    """Inspect a compressed file or archive and show metadata.
    \f

    An archive reports its format, entry count, and total size; pass
    `--entries` to also list each entry's own mtime, permissions, and (for a
    format like zip that tracks it) per-entry compressed size and CRC. A
    single-file container, a Compresso `.comp` or a standalone codec, reports
    its algorithm and decompression info as before.

    Args:
        file: The path to the compressed file or archive.
        output_json: If True, output metadata in JSON format.
        show_entries: If True and the file is an archive, also list each
            entry's own detail.
    """
    try:
        if looks_like_archive(file):
            _inspect_archive(file, output_json, show_entries)
            return

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

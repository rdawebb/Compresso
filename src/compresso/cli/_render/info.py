"""What describes an archive or a single-file container: its entries and metadata."""

from __future__ import annotations

import stat
import time
from pathlib import Path

import orjson

from ...frontend.archive_api import ArchiveEntry
from ...introspect.file_inspect import InspectResult
from .._app import app
from . import EXIT_USAGE, fail, format_size, format_time, succeed


def list_entries(entries: list[ArchiveEntry]) -> None:
    """Print one line per entry: its size, then its path.

    Directories and symlinks leave the size blank, a symlink is followed by
    its target, and each entry is indented once for every directory above it
    that was already listed.

    Args:
        entries: The archive's entries, in archive order.
    """
    listed_dirs: set[str] = set()

    for entry in entries:
        parts = entry.path.rstrip("/").split("/")
        depth = sum("/".join(parts[:i]) in listed_dirs for i in range(1, len(parts)))

        if entry.is_dir:
            listed_dirs.add("/".join(parts))

        # A symlink's stored size is the length of its target; hardlink's have no data
        size = (
            ""
            if entry.is_dir or entry.is_symlink or entry.is_hardlink
            else format_size(size_bytes=entry.size)
        )
        target = f" -> {entry.link_target}" if entry.is_symlink else ""

        # "1023.99 KB" is the widest format_size gives
        print(f"{size:>10}  {'  ' * depth}{entry.path}{target}")


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
    has_no_data = entry.is_dir or entry.is_symlink or entry.is_hardlink
    size = "" if has_no_data else format_size(size_bytes=entry.size)

    if entry.is_symlink:
        target = f" -> {entry.link_target}"
    elif entry.is_hardlink:
        target = f" link to {entry.link_target}"
    else:
        target = ""

    detail = ""
    if entry.compressed_size is not None:
        detail = f"  (compressed {format_size(size_bytes=entry.compressed_size)}"
        if entry.crc is not None:
            detail += f", crc {entry.crc:08x}"
        if entry.method is not None:
            detail += f", method {entry.method}"
        detail += ")"

    return f"{mode}  {mtime}  {size:>10}  {entry.path}{target}{detail}"


def archive_info(
    file: Path,
    fmt: str | None,
    entries: list[ArchiveEntry],
    *,
    as_json: bool,
    show_entries: bool,
) -> None:
    """Print an archive's metadata, as text or JSON.

    Args:
        file: The archive's path.
        fmt: The archive's detected format.
        entries: The archive's entries.
        as_json: If True, print JSON instead of text.
        show_entries: If True, also print each entry's own detail.
    """
    total_size = sum(entry.size for entry in entries)
    archive_size: int = file.stat().st_size

    if as_json:
        data: dict[str, object] = {
            "path": str(file),
            "format": fmt,
            "is_archive": True,
            "entry_count": len(entries),
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
                    "is_hardlink": entry.is_hardlink,
                    "mtime": entry.mtime,
                    "mode": entry.mode,
                    "link_target": entry.link_target,
                    "compressed_size": entry.compressed_size,
                    "crc": entry.crc,
                    "method": entry.method,
                }
                for entry in entries
            ]
        print(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode())
        return

    lines: list[str] = [
        f"File:          {file}",
        f"Format:        {fmt}",
        f"Entries:       {len(entries)}",
        f"Total size:    {format_size(size_bytes=total_size)}",
        f"Archive size:  {format_size(size_bytes=archive_size)}",
    ]

    if total_size:
        ratio: int | float = (archive_size / total_size) * 100
        lines.append(f"Compression:   {ratio:.1f}% of original")

    print("\n".join(lines))

    if show_entries:
        print("\n" + "\n".join(_entry_detail_line(entry) for entry in entries))


def file_info(result: InspectResult, *, as_json: bool) -> None:
    """Print a single-file container's metadata, as text or JSON.

    Args:
        result: The container's inspection.
        as_json: If True, print JSON instead of text.
    """
    if as_json:
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
            "can_decompress": result.can_decompress,
            "estimated_decomp_s": result.estimated_decomp_s,
            "reason": result.reason,
        }
        print(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode())
        return

    print(f"File: {result.path}\n")

    if not result.is_compresso:
        reason = f"\n  Reason: {result.reason}" if result.reason else ""
        fail(f"Not a valid Compresso file{reason}", EXIT_USAGE)

    if not result.header_ok:
        reason = f"\n  Reason: {result.reason}" if result.reason else ""
        fail(f"Invalid file header{reason}", EXIT_USAGE)

    succeed("Valid Compresso file\n")

    lines: list[str] = [
        f"Algorithm:       {result.algo_name or 'Unknown'} (ID: {result.algo_id})",
        f"Version:         {result.version}",
        "",
    ]

    if result.level is not None:
        lines.append(f"Level:           {result.level}")
    else:
        lines.append("Level:           auto")

    if result.orig_size:
        lines.append(f"Original size:   {format_size(size_bytes=result.orig_size)}")

    compressed_size: int = result.path.stat().st_size
    lines.append(f"Compressed size: {format_size(size_bytes=compressed_size)}")

    if result.orig_size:
        ratio: int | float = (compressed_size / result.orig_size) * 100
        lines.append(f"Compression:     {ratio:.1f}% of original")

    lines += [
        "",
        f"Backend available:  {'Yes' if result.backend_available else 'No'}",
        f"Can decompress:     {'Yes' if result.can_decompress else 'No'}",
    ]

    if result.estimated_decomp_s:
        lines.append(
            f"Est. decomp time:   {format_time(seconds=result.estimated_decomp_s)}"
        )
        lines.append("")

    print("\n".join(lines))

    if not result.can_decompress and result.reason:
        app.echo(message=app.style(text=f"\n⚠ {result.reason}", fg="yellow"))

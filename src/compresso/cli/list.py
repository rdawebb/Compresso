"""The `list` command, showing an archive's entries."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from .._core import detect_format
from ..frontend.archive_api import ArchiveEntry
from ..frontend.archive_api import plan_extraction as plan_archive_extraction
from ..frontend.dispatch import is_archive_format
from ._app import app
from ._render import cancelled, fail, input_header
from ._render.info import list_entries, print_json
from ._run import InputFailed, require_files, run_all


def _read_entries(path: Path) -> tuple[str, list[ArchiveEntry]]:
    """Read one archive's format and entries.

    Args:
        path: The archive.

    Returns:
        The detected format and the entries, in archive order.

    Raises:
        InputFailed: If the input is not an archive or can't be read.
    """
    fmt = detect_format(path)

    if not is_archive_format(fmt):
        if fmt == "unknown":
            raise InputFailed(f"{path}: not a recognised archive")

        raise InputFailed(f"{path}: holds a single file, so there is nothing to list")

    plan = plan_archive_extraction(archive=path)

    if not plan.can_run:
        raise InputFailed(f"{path}: {plan.reason_if_unavailable}")

    return fmt, plan.entries


def list_archives(
    inputs: Annotated[
        list[Path], app.Argument(help="Archives to list", readable=False)
    ],
    long: Annotated[
        bool,
        app.Option("--long", "-l", help="Show each entry's mode, mtime and detail"),
    ] = False,
    output_json: Annotated[
        bool, app.Option("--json", help="Output a JSON array, one item per archive")
    ] = False,
) -> None:
    """List the entries of archives.
    \f

    Each entry is shown with its size and path, indented under the directories
    listed above it; `--long` adds its mode and mtime, and (for a format like
    zip that tracks it) its compressed size and CRC.

    Args:
        inputs: The archives to list.
        long: If True, show each entry's full detail.
        output_json: If True, print a JSON array of `{path, format, entries}`.
    """
    require_files(inputs)

    listings: list[dict[str, object]] = []

    def run_one(path: Path) -> None:
        fmt, entries = _read_entries(path)

        if not output_json:
            if len(inputs) > 1:
                input_header(path, first=not listings)

            list_entries(entries, long=long)

        listings.append({"path": str(path), "format": fmt, "entries": entries})

    try:
        code = run_all(inputs, run_one, "Listing")

        if output_json:
            print_json(listings)

        sys.exit(code)

    except KeyboardInterrupt:
        cancelled("Listing")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(name="list", aliases=["l"])(list_archives)

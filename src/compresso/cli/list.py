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
from ._render import EXIT_USAGE, cancelled, fail, input_header
from ._render.info import list_entries, listings_json
from ._run import InputFailed, run_all


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
    inputs: Annotated[list[Path], app.Argument(help="Archives to list")],
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
    for path in inputs:
        if not path.is_file():
            fail(f"Error: {path} does not exist or is not a file", EXIT_USAGE)

    listings: list[tuple[Path, str, list[ArchiveEntry]]] = []

    def run_one(path: Path) -> None:
        fmt, entries = _read_entries(path)

        if not output_json:
            if len(inputs) > 1:
                input_header(path, first=not listings)

            list_entries(entries, long=long)

        listings.append((path, fmt, entries))

    try:
        code = run_all(inputs, run_one, "Listing")

        if output_json:
            listings_json(listings)

        sys.exit(code)

    except KeyboardInterrupt:
        cancelled("Listing")

    except Exception as e:
        fail(f"Unexpected error: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(name="list", aliases=["l"])(list_archives)

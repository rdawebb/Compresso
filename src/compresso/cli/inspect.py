"""The `inspect` command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from .._core import detect_format
from ..frontend.archive_api import plan_extraction as plan_archive_extraction
from ..frontend.dispatch import looks_like_archive
from ..introspect.file_inspect import inspect as inspect_file
from ._app import app
from ._render import EXIT_USAGE, cancelled, fail
from ._render.info import archive_info, file_info


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

    archive_info(
        file,
        detect_format(file),
        plan.entries,
        as_json=output_json,
        show_entries=show_entries,
    )


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

        file_info(inspect_file(path=file), as_json=output_json)

    except KeyboardInterrupt:
        cancelled("Inspection")

    except Exception as e:
        fail(f"Error inspecting file: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["i", "info"])(inspect)

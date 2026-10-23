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


def _inspect_archive(file: Path, output_json: bool) -> None:
    """Inspect an archive: a container that can hold many entries.

    Args:
        file: The path to the archive.
        output_json: If True, output metadata in JSON format.
    """
    plan = plan_archive_extraction(archive=file)

    if not plan.can_run:
        fail(f"Error: {plan.reason_if_unavailable}", EXIT_USAGE)

    archive_info(file, detect_format(file), plan.entries, as_json=output_json)


def inspect(
    file: Annotated[Path, app.Argument(help="File to inspect")],
    output_json: Annotated[
        bool, app.Option("--json", help="Output in JSON format")
    ] = False,
) -> None:
    """Inspect a compressed file or archive and show metadata.
    \f

    An archive reports its format, entry count, and total size; `list` shows
    its entries. A single-file container, a Compresso `.comp` or a standalone
    codec, reports its algorithm and decompression info as before.

    Args:
        file: The path to the compressed file or archive.
        output_json: If True, output metadata in JSON format.
    """
    try:
        if looks_like_archive(file):
            _inspect_archive(file, output_json)
            return

        file_info(inspect_file(path=file), as_json=output_json)

    except KeyboardInterrupt:
        cancelled("Inspection")

    except Exception as e:
        fail(f"Error inspecting file: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["i", "info"])(inspect)

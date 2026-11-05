"""The `info` command, describing archives and compressed files."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated

from .._core import detect_format, gzip_stored_name, stored_content_size
from ..frontend._formats import gzip_stored_mtime
from ..frontend.archive_api import plan_extraction as plan_archive_extraction
from ..frontend.dispatch import is_archive_format
from ..introspect.file_inspect import inspect as inspect_file
from ._app import app
from ._render import handles_errors, input_header
from ._render.info import archive_info, codec_info, file_info, print_json
from ._run import InputFailed, require_files, run_all


def _describer(path: Path) -> Callable[..., dict[str, object]]:
    """Read one input, returning the call that describes it.

    Args:
        path: The input.

    Returns:
        A render call taking `as_json`, which prints the input unless JSON is
        wanted and returns its JSON item.

    Raises:
        InputFailed: If the input is unrecognised or can't be read.
    """
    fmt = detect_format(path)

    if is_archive_format(fmt):
        plan = plan_archive_extraction(archive=path)
        if not plan.can_run:
            raise InputFailed(f"{path}: {plan.reason_if_unavailable}")

        return partial(archive_info, path, fmt, plan.entries)

    if fmt == "compresso":
        result = inspect_file(path=path)
        if not result.header_ok:
            raise InputFailed(f"{path}: {result.reason}")

        return partial(file_info, result)

    if fmt == "unknown":
        raise InputFailed(f"{path}: not a recognised archive or compressed file")

    stored: bytes | None = gzip_stored_name(path)

    return partial(
        codec_info,
        path,
        fmt,
        # Only for display, so a name that isn't UTF-8 can stay approximate
        stored_name=stored.decode(errors="replace") if stored else None,
        mtime=gzip_stored_mtime(path) if fmt == "gzip" else None,
        orig_size=stored_content_size(path),
    )


@handles_errors("Inspection")
def info(
    inputs: Annotated[
        list[Path], app.Argument(help="Files to describe", readable=False)
    ],
    output_json: Annotated[
        bool, app.Option("--json", help="Output a JSON array, one item per input")
    ] = False,
) -> None:
    """Describe archives and compressed files.
    \f

    An archive reports its format, entry count, and total size; `list` shows
    its entries. A Compresso `.comp` reports its algorithm and decompression
    info, and a standalone codec file its format and size, with whatever its
    header stores: a gzip's name and mtime, an xz, zstd, lz4 or snappy file's
    original size.

    Args:
        inputs: The archives or compressed files to describe.
        output_json: If True, print a JSON array, one item per input.
    """
    require_files(inputs)

    items: list[dict[str, object]] = []

    def run_one(path: Path) -> None:
        describe = _describer(path)

        if len(inputs) > 1 and not output_json:
            input_header(path, first=not items)

        items.append(describe(as_json=output_json))

    code = run_all(inputs, run_one, "Inspection")

    if output_json:
        print_json(items)

    sys.exit(code)


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["i"])(info)

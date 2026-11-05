"""Main CLI for Compresso compression and decompression tool."""

from __future__ import annotations

import re
import sys
from typing import Any, TextIO

from . import benchmark, compress, extract, formats, info, verify
from . import list as list_
from ._app import app

__all__ = ["app", "main"]

# The order commands are listed in `--help`
_COMMAND_MODULES = (
    compress,
    extract,
    verify,
    list_,
    info,
    formats,
    benchmark,
)

for _module in _COMMAND_MODULES:
    _module.register()


# Styling and spaces, so Rich's padded blank lines count as blank
_INVISIBLE = re.compile(r"\x1b\[[0-9;]*m| ")


class _Padded:
    """A terminal stream that gives a run's output one blank line above it.

    main() adds the one below. Output that already starts or ends with a blank
    line, as `--help` does, gets no extra.
    """

    # Shared, so stdout and stderr pad once between them
    tail: str | None = None  # the last two visible characters written
    last: _Padded | None = None

    def __init__(self, stream: TextIO) -> None:
        """Initialise a padded stream.

        Args:
            stream: The underlying stream to write to.
        """
        self._stream = stream

    def write(self, data: str) -> int:
        """Write to the underlying stream, with a blank line above if needed.

        Args:
            data: The string to write.

        Returns:
            The number of characters written.
        """
        # Typer/Click probes for a binary stream with write(b"")
        visible = _INVISIBLE.sub("", data) if isinstance(data, str) else ""
        if visible:
            if _Padded.tail is None and not visible.startswith("\n"):
                self._stream.write("\n")

            _Padded.tail = ((_Padded.tail or "") + visible)[-2:]
            _Padded.last = self

        return self._stream.write(data)

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the underlying stream.

        Args:
            name: The name of the attribute to access.

        Returns:
            The value of the requested attribute.
        """
        return getattr(self._stream, name)


def main() -> None:
    """Entry point for the CLI."""
    # Not when piped, as tar and gzip don't pad what a script reads
    if sys.stdout.isatty():
        sys.stdout = _Padded(sys.stdout)

    if sys.stderr.isatty():
        sys.stderr = _Padded(sys.stderr)

    try:
        app()

    finally:
        if _Padded.last and _Padded.tail != "\n\n":
            _Padded.last.write("\n")


if __name__ == "__main__":
    main()

"""Main CLI for Compresso compression and decompression tool."""

from __future__ import annotations

from . import algos, benchmark, compress, extract, inspect
from ._app import app

__all__ = ["app", "main"]

# The order commands are listed in `--help`
_COMMAND_MODULES = (
    compress,
    extract,
    inspect,
    benchmark,
    algos,
)

for _module in _COMMAND_MODULES:
    _module.register()


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

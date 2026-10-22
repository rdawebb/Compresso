"""Tests for `compresso list`."""

from __future__ import annotations

import subprocess
import sys

from compresso.cli._render import (
    EXIT_OK,
)

from .conftest import invoke


class TestList:
    """Test the read-only `list` command."""

    def test_runs_as_a_module(self) -> None:
        """Test that `python -m compresso.cli` works, which a package needs __main__ for."""
        result = subprocess.run(
            [sys.executable, "-m", "compresso.cli", "list"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == EXIT_OK
        assert "zstd" in result.stdout

    def test_list_shows_level_ranges(self) -> None:
        """Test that list shows each backend's levels, and none for snappy."""
        output = invoke("list").output

        zstd = output[output.index("- zstd") :].split("\n\n")[0]
        snappy = output[output.index("- snappy") :].split("\n\n")[0]
        assert "Levels: 1-22" in zstd
        assert "Levels: none" in snappy

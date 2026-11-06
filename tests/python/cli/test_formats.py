"""Tests for `compresso formats`."""

from __future__ import annotations

import subprocess
import sys

from compresso.cli._render import (
    EXIT_OK,
)

from .conftest import invoke


def section(output: str, name: str) -> str:
    """Return one `- name` block of the output, up to the blank line after it."""
    return output[output.index(f"- {name}\n") :].split("\n\n")[0]


class TestFormats:
    """Test the read-only `formats` command."""

    def test_runs_as_a_module(self) -> None:
        """Test that `python -m compresso.cli` works, which a package needs __main__ for."""
        result = subprocess.run(
            [sys.executable, "-m", "compresso.cli", "formats"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == EXIT_OK
        assert "zstd" in result.stdout

    def test_formats_shows_level_ranges(self) -> None:
        """Test that formats shows each backend's levels, and none for snappy."""
        output = invoke("formats").output

        assert "Levels: 1-22" in section(output, "zstd")
        assert "Levels: none" in section(output, "snappy")

    def test_formats_shows_standalone_and_archive_formats(self) -> None:
        """Test that each standalone format shows its aliases and extensions, and
        tar its compressed variants, named as an output would be."""
        output = invoke("formats").output
        standalone = output[output.index("Standalone formats") :]

        assert "Aliases:    zst" in section(standalone, "zstd")
        assert "Extensions: .zst, .zstd" in section(standalone, "zstd")
        assert "Aliases:    none" in section(standalone, "lz4")
        assert "tar.gz, tar.bz2, tar.xz, tar.zst, tar.lz4, tar.sz" in section(
            output, "tar"
        )
        assert section(output, "zip") == "- zip"

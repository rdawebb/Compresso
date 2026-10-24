"""Tests for `compresso info`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import orjson

from compresso.cli._render import EXIT_FAILED

from .conftest import invoke


class TestInfo:
    """Test the read-only `info` command."""

    def test_info_reports_a_valid_file(self, payload: Path, tmp_path: Path) -> None:
        """Test that info describes a file the CLI just wrote."""
        archive = tmp_path / "out.comp"
        invoke("compress", payload, "-o", archive, "-q")

        result = invoke("info", archive)

        assert "Valid Compresso file" in result.output

    def test_info_archive_text(self, make_archive: Callable[[str], Path]) -> None:
        """Test that an archive shows a summary, not its entries."""
        summary = invoke("info", make_archive("zip")).output

        assert "Entries:       4" in summary
        assert "f0.bin" not in summary

    def test_standalone_codec_shows_format_and_size(
        self, payload: Path, tmp_path: Path
    ) -> None:
        """Test that a .gz is described by its format, not refused as a bad .comp."""
        packed = tmp_path / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        output = invoke("info", packed).output

        assert "Format:        gzip" in output
        assert "Size:" in output

    def test_several_inputs_carry_on_past_an_unrecognised_one(
        self, payload: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that each described input gets a header, and an unrecognised one
        fails alone, exiting 1 once the rest are described."""
        archive = make_archive("tar")
        packed = tmp_path / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        result = invoke("info", archive, payload, packed, expect=EXIT_FAILED)

        assert f"{payload}: not a recognised archive" in result.output
        assert result.stdout.startswith(f"==> {archive} <==\n")
        assert f"\n\n==> {packed} <==\n" in result.stdout
        assert f"==> {payload} <==" not in result.stdout

    def test_json_is_an_array_per_input(
        self, payload: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --json is one array item per input, whatever its kind."""
        comp, packed = tmp_path / "out.comp", tmp_path / "p.gz"
        invoke("compress", payload, "-o", comp, "-q")
        invoke("compress", payload, "-o", packed, "-q")

        items = orjson.loads(
            invoke("info", comp, make_archive("zip"), packed, "--json").output
        )

        assert [item["format"] for item in items] == ["compresso", "zip", "gzip"]
        assert items[0]["is_compresso"] is True
        assert items[1]["entry_count"] == 4
        assert items[2]["size"] == packed.stat().st_size

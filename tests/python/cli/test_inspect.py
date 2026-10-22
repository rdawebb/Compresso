"""Tests for `compresso inspect`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import orjson

from .conftest import invoke


class TestInspect:
    """Test the read-only `inspect` command."""

    def test_inspect_reports_a_valid_file(self, payload: Path, tmp_path: Path) -> None:
        """Test that inspect describes a file the CLI just wrote."""
        archive = tmp_path / "out.comp"
        invoke("compress", payload, "-o", archive, "-q")

        result = invoke("inspect", archive)

        assert "Valid Compresso file" in result.output

    def test_inspect_json(self, payload: Path, tmp_path: Path) -> None:
        """Test that --json emits parseable output."""
        archive = tmp_path / "out.comp"
        invoke("compress", payload, "-o", archive, "-q")

        result = invoke("inspect", archive, "--json")

        assert orjson.loads(result.output)["is_compresso"] is True

    def test_inspect_archive_text(self, make_archive: Callable[[str], Path]) -> None:
        """Test that an archive shows only a summary unless --entries is given."""
        archive = make_archive("zip")

        summary = invoke("inspect", archive).output
        listing = invoke("inspect", archive, "--entries").output

        assert "Entries:       4" in summary
        assert "f0.bin" not in summary
        assert "f0.bin" in listing

    def test_inspect_archive_json(self, make_archive: Callable[[str], Path]) -> None:
        """Test that --json carries entries only when --entries is given."""
        archive = make_archive("zip")

        summary = orjson.loads(invoke("inspect", archive, "--json").output)
        listing = orjson.loads(invoke("inspect", archive, "--entries", "--json").output)

        assert summary["entry_count"] == 4
        assert "entries" not in summary
        assert sum("f0.bin" in e["path"] for e in listing["entries"]) == 1

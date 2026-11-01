"""Tests for `compresso test`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from compresso.cli._render import EXIT_FAILED

from ..helpers import flip_middle_byte
from .conftest import invoke


@pytest.fixture
def packed(payload: Path, tmp_path: Path) -> Path:
    """`payload` as a gzip file."""
    path = tmp_path / "p.gz"
    invoke("compress", payload, "-o", path, "-q")

    return path


class TestVerify:
    """Test the `test` command."""

    def test_good_input_says_ok(self, packed: Path) -> None:
        """Test that each input gets one line naming it."""
        assert f"OK: {packed}" in invoke("test", packed).output

    def test_quiet_prints_nothing(self, packed: Path) -> None:
        """Test that -q leaves only the exit code."""
        assert invoke("test", "-q", packed).output == ""

    def test_damage_fails_and_the_rest_still_run(
        self, packed: Path, tmp_path: Path
    ) -> None:
        """Test that a bad input is named, and the next input is still checked."""
        bad = tmp_path / "bad.gz"
        shutil.copy(packed, bad)
        flip_middle_byte(bad)

        output = invoke("test", bad, packed, expect=EXIT_FAILED).output

        assert "Test failed" in output
        assert f"OK: {packed}" in output

    def test_trailing_data_fails(self, packed: Path) -> None:
        """Test that it fails here, as with `gzip -t`, though `extract` only warns."""
        packed.write_bytes(packed.read_bytes() + b"not gzip data")

        output = invoke("test", packed, expect=EXIT_FAILED).output

        assert "trailing data" in output

    def test_unrecognised_file_fails(self, tmp_path: Path) -> None:
        """Test that a file of no known format is an input failure."""
        notes = tmp_path / "notes.txt"
        notes.write_text("just some text")

        assert "not a recognised" in invoke("test", notes, expect=EXIT_FAILED).output

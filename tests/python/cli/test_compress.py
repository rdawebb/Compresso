"""Tests for `compresso compress`."""

from __future__ import annotations

from pathlib import Path

import pytest

from compresso.cli._render import (
    EXIT_USAGE,
)

from ..helpers import renamed
from .conftest import CONFLICT_MODES, CompressTarget, invoke


class TestCompressRoundTrip:
    """Test compress and decompress, end to end."""

    def test_compress_then_decompress(self, payload: Path, tmp_path: Path) -> None:
        """Test that a file survives a round trip through the CLI unchanged."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"

        result = invoke("compress", payload, "-o", archive)
        assert "Compression successful" in result.output

        result = invoke("extract", archive, "-o", restored)
        assert "Decompression successful" in result.output

        assert restored.read_bytes() == payload.read_bytes()

    def test_quiet_suppresses_output(self, payload: Path, tmp_path: Path) -> None:
        """Test that --quiet prints nothing on success."""
        result = invoke("compress", payload, "-o", tmp_path / "q.comp", "-q")
        assert result.output.strip() == ""

    # The backends are covered in core and C tests; this checks the flags
    @pytest.mark.parametrize(
        "args", [("-a", "bzip2"), ("-a", "zstd", "-l", "19")], ids=["algo", "level"]
    )
    def test_algorithms_round_trip(
        self, payload: Path, tmp_path: Path, args: tuple[str, ...]
    ) -> None:
        """Test that -a and -l reach the backend."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"

        invoke("compress", payload, "-o", archive, *args, "-q")
        invoke("extract", archive, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()


class TestCompressConflicts:
    """Test what `compress` does when its output already exists."""

    @CONFLICT_MODES
    def test_existing_output(
        self,
        compress_target: CompressTarget,
        flags: tuple[str, ...],
        expect: int,
        kept: bool,
        renames: bool,
    ) -> None:
        """Test that each overwrite mode keeps, replaces or renames around the output."""
        t = compress_target
        t.dest.write_bytes(b"stale")

        invoke("compress", t.source, "-o", t.dest, *t.args, *flags, "-q", expect=expect)

        assert (t.dest.read_bytes() == b"stale") == kept
        assert (t.dest.parent / renamed(t.dest.name)).is_file() == renames


class TestSmartCompress:
    """Test `compress` runs the job dispatch plans; the rules are in frontend/test_dispatch.py."""

    def test_a_directory_round_trips_as_an_archive(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a directory is written as the default archive and comes back."""
        invoke("compress", source_tree, "-q")

        dest = tmp_path / "back"
        invoke(
            "extract",
            source_tree.with_name(f"{source_tree.name}.zip"),
            "-o",
            dest,
            "-q",
        )

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_single_file_format_refuses_several_inputs(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a .gz holds one file, so a directory cannot go into one."""
        result = invoke(
            "compress",
            source_tree,
            "-f",
            "gz",
            "-o",
            tmp_path / "o.gz",
            expect=EXIT_USAGE,
        )

        assert "single file" in result.output

    def test_unknown_format_is_refused(self, payload: Path) -> None:
        """Test that an unrecognised -f names neither container."""
        invoke("compress", payload, "-f", "bogus", expect=EXIT_USAGE)

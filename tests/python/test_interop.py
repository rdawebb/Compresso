"""Tests against files made by the reference tools, not by Compresso.

The fixtures come from scripts/interop_fixtures.sh.
"""

import stat
import sys
from pathlib import Path

import pytest

from compresso import _core

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INTEROP = FIXTURES / "interop"

PART1 = (INTEROP / "part1.txt").read_bytes()
PART2 = (INTEROP / "part2.txt").read_bytes()
ALICE = (FIXTURES / "alice29.txt").read_bytes()


def _case(fixture: str, expected: bytes, *, first_stream_only: bool = False) -> object:
    """One decoding case, named by its fixture.

    Args:
        fixture: The file under tests/fixtures/interop.
        expected: What the whole file decodes to.
        first_stream_only: Whether the decoder still stops after the first
            stream, making the case an expected failure.

    Returns:
        The pytest parameter set.
    """
    marks = (
        [pytest.mark.xfail(reason="decodes only the first stream")]
        if first_stream_only
        else []
    )

    return pytest.param(fixture, expected, id=fixture, marks=marks)


class TestStandaloneDecoding:
    """Test decoding single-file containers from the reference tools."""

    @pytest.mark.parametrize(
        ("fixture", "expected"),
        [
            _case("gzip_single.gz", PART1),
            _case("bzip2_single.bz2", PART1),
            _case("xz_single.xz", PART1),
            _case("lz4_single.lz4", PART1),
            _case("zstd_single.zst", PART1),
            _case("gzip_multi_member.gz", PART1 + PART2, first_stream_only=True),
            _case("gzip_bgzf.gz", ALICE, first_stream_only=True),
            _case("bzip2_pbzip2.bz2", ALICE, first_stream_only=True),
            _case("xz_concat.xz", PART1 + PART2, first_stream_only=True),
            _case("lz4_concat.lz4", PART1 + PART2, first_stream_only=True),
            _case("lz4_skippable.lz4", PART1 + PART2, first_stream_only=True),
            _case("zstd_concat.zst", PART1 + PART2),
            _case("zstd_skippable.zst", PART1 + PART2),
        ],
    )
    def test_decodes_every_stream(
        self, temp_dir: Path, fixture: str, expected: bytes
    ) -> None:
        """Test that the whole file decodes, not just its first stream."""
        restored = temp_dir / "restored"

        _core.decompress_standalone(str(INTEROP / fixture), str(restored))

        assert restored.read_bytes() == expected


class TestTarExtraction:
    """Test extracting tars made by GNU tar."""

    @pytest.mark.xfail(reason="hardlinks are refused as special files")
    def test_hardlink_gets_its_target_contents(self, temp_dir: Path) -> None:
        """Test that a hardlink entry extracts with the contents of its target."""
        _core.extract_archive(str(INTEROP / "tar_hardlink.tar"), str(temp_dir), [])

        assert (temp_dir / "hardlink" / "a.txt").read_bytes() == PART1
        assert (temp_dir / "hardlink" / "b.txt").read_bytes() == PART1

    # A read-only directory still accepts new files on Windows
    @pytest.mark.xfail(
        sys.platform != "win32",
        reason="the 0555 mode is applied before the directory is filled",
    )
    def test_read_only_directory_is_filled_before_its_mode_is_set(
        self, temp_dir: Path
    ) -> None:
        """Test that a 0555 directory entry doesn't block the files inside it."""
        _core.extract_archive(str(INTEROP / "tar_readonly_dir.tar"), str(temp_dir), [])

        directory = temp_dir / "readonly"
        assert (directory / "file.txt").read_bytes() == PART1
        if sys.platform != "win32":
            assert stat.S_IMODE(directory.stat().st_mode) == 0o555


class TestZipSymlinks:
    """Test a zip made by Info-ZIP's `zip -y`, which stores a symlink."""

    def test_symlink_extracts_as_a_file_holding_its_target(
        self, temp_dir: Path
    ) -> None:
        """Test that the stored link becomes a regular file of its target text."""
        _core.extract_archive(str(INTEROP / "zip_symlink.zip"), str(temp_dir), [])

        link = temp_dir / "symlink" / "link.txt"
        assert not link.is_symlink()
        assert link.read_bytes() == b"target.txt"

    @pytest.mark.xfail(reason="the external attributes aren't read")
    def test_symlink_is_listed_as_a_symlink(self) -> None:
        """Test that listing reports the stored link as a symlink with a target."""
        entries = {
            entry["path"]: entry
            for entry in _core.list_archive_contents(str(INTEROP / "zip_symlink.zip"))
        }

        assert entries["symlink/link.txt"]["type"] == "symlink"
        assert entries["symlink/link.txt"]["link_target"] == "target.txt"

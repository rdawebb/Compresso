"""Tests against files made by the reference tools, not by Compresso.

The fixtures come from scripts/interop_fixtures.sh.
"""

import gzip
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from compresso import CorruptDataError, TrailingDataWarning, _core, plan_extraction

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INTEROP = FIXTURES / "interop"

PART1 = (INTEROP / "part1.txt").read_bytes()
PART2 = (INTEROP / "part2.txt").read_bytes()
ALICE = (FIXTURES / "alice29.txt").read_bytes()

# A skippable frame (zstd and lz4 share the format): magic 0x184D2A50, a u32 LE
# length, then that many bytes to ignore
SKIPPABLE = b"\x50\x2a\x4d\x18\x04\x00\x00\x00SKIP"


def _case(fixture: str, expected: bytes) -> object:
    """One decoding case, named by its fixture.

    Args:
        fixture: The file under tests/fixtures/interop.
        expected: What the whole file decodes to.

    Returns:
        The pytest parameter set.
    """
    return pytest.param(fixture, expected, id=fixture)


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
            _case("sz_single.sz", PART1),
            _case("gzip_multi_member.gz", PART1 + PART2),
            _case("gzip_bgzf.gz", ALICE),
            _case("bzip2_pbzip2.bz2", ALICE),
            _case("xz_concat.xz", PART1 + PART2),
            _case("lz4_concat.lz4", PART1 + PART2),
            _case("lz4_skippable.lz4", PART1 + PART2),
            _case("zstd_concat.zst", PART1 + PART2),
            _case("zstd_skippable.zst", PART1 + PART2),
            # The gzip file doesn't compress, so it's stored as is
            _case("sz_uncompressed.sz", (INTEROP / "gzip_single.gz").read_bytes()),
            _case("sz_padding.sz", PART1),
            _case("sz_concat.sz", PART1 + PART2),
        ],
    )
    def test_decodes_every_stream(
        self, temp_dir: Path, fixture: str, expected: bytes
    ) -> None:
        """Test that the whole file decodes, not just its first stream."""
        restored = temp_dir / "restored"

        _core.decompress_standalone(str(INTEROP / fixture), str(restored))

        assert restored.read_bytes() == expected

    @pytest.mark.parametrize("fixture", ["gzip_single.gz", "bzip2_single.bz2"])
    @pytest.mark.parametrize(
        "trailing", [b"junk", bytes(512)], ids=["garbage", "zero-padding"]
    )
    def test_warns_about_trailing_data_and_keeps_the_output(
        self, temp_dir: Path, fixture: str, trailing: bytes
    ) -> None:
        """Test that data after the last stream is ignored with a warning.

        RFC 1952 allows it for gzip, and the gzip and bzip2 tools both only
        warn; tape-blocked archives end in zero padding.
        """
        single = (INTEROP / fixture).read_bytes()
        source = temp_dir / f"trailing-{fixture}"
        source.write_bytes(single + trailing)
        restored = temp_dir / "restored"

        with pytest.warns(TrailingDataWarning, match=f"from byte {len(single)},"):
            _core.decompress_standalone(str(source), str(restored))

        assert restored.read_bytes() == PART1

    @pytest.mark.parametrize(
        ("padding", "between"),
        [(bytes(4), False), (bytes(8), True)],
        ids=["after", "between"],
    )
    def test_xz_skips_stream_padding(
        self, temp_dir: Path, padding: bytes, between: bool
    ) -> None:
        """Test that xz's Stream Padding, null bytes in fours, is skipped silently."""
        single = (INTEROP / "xz_single.xz").read_bytes()
        source = temp_dir / "padded.xz"
        source.write_bytes(single + padding + (single if between else b""))
        restored = temp_dir / "restored"

        _core.decompress_standalone(str(source), str(restored))

        assert restored.read_bytes() == (PART1 + PART1 if between else PART1)

    def test_xz_decodes_an_unsupported_check_with_a_warning(
        self, temp_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that an unknown check type is logged and skipped."""
        restored = temp_dir / "restored"

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.decompress_standalone(
                str(INTEROP / "xz_unsupported_check.xz"), str(restored)
            )

        assert restored.read_bytes() == PART1
        assert [r.getMessage() for r in caplog.records] == [
            (
                "xz stream has an unsupported integrity check type; not "
                "verifying its integrity"
            )
        ]

    def test_xz_with_a_known_check_logs_nothing(
        self, temp_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that the warning is specific to the unsupported check type."""
        with caplog.at_level(logging.DEBUG, logger="compresso"):
            _core.decompress_standalone(
                str(INTEROP / "xz_single.xz"), str(temp_dir / "restored")
            )

        assert not caplog.records

    @pytest.mark.parametrize(
        "trailing", [b"junk", bytes(3)], ids=["garbage", "unaligned-padding"]
    )
    def test_xz_rejects_trailing_data(self, temp_dir: Path, trailing: bytes) -> None:
        """Test that xz, as the xz tool does, fails on anything but padding."""
        source = temp_dir / "trailing.xz"
        source.write_bytes((INTEROP / "xz_single.xz").read_bytes() + trailing)
        restored = temp_dir / "restored"

        with pytest.raises(CorruptDataError):
            _core.decompress_standalone(str(source), str(restored))

        assert not restored.exists()

    def test_lz4_skips_a_trailing_skippable_frame(self, temp_dir: Path) -> None:
        """Test that a skippable frame after the last frame decodes to nothing."""
        source = temp_dir / "skippable.lz4"
        source.write_bytes((INTEROP / "lz4_single.lz4").read_bytes() + SKIPPABLE)
        restored = temp_dir / "restored"

        _core.decompress_standalone(str(source), str(restored))

        assert restored.read_bytes() == PART1

    @pytest.mark.parametrize(
        "trailing", [b"junk", bytes(8)], ids=["garbage", "zero-padding"]
    )
    def test_lz4_rejects_trailing_data(self, temp_dir: Path, trailing: bytes) -> None:
        """Test that lz4, as the lz4 tool does, fails on anything but a frame."""
        source = temp_dir / "trailing.lz4"
        source.write_bytes((INTEROP / "lz4_single.lz4").read_bytes() + trailing)
        restored = temp_dir / "restored"

        with pytest.raises(CorruptDataError):
            _core.decompress_standalone(str(source), str(restored))

        assert not restored.exists()

    @pytest.mark.parametrize("algo", ["zlib", "bzip2", "lzma", "lz4"])
    def test_comp_rejects_data_after_its_payload(
        self, temp_dir: Path, algo: str
    ) -> None:
        """Test that .comp, which holds exactly one stream, has no such leeway."""
        compressed = temp_dir / "part1.comp"
        _core.compress_file(
            str(INTEROP / "part1.txt"), str(compressed), algo, "balanced", -1
        )
        compressed.write_bytes(compressed.read_bytes() + bytes(4))
        restored = temp_dir / "restored"

        with pytest.raises(CorruptDataError, match=f"after the end of a {algo} stream"):
            _core.decompress_file(str(compressed), str(restored), "")

        assert not restored.exists()


class TestDetection:
    """Test detecting formats whose first bytes aren't their usual magic."""

    @pytest.mark.parametrize(
        ("fixture", "expected"),
        [("zstd_single.zst", "zstd"), ("lz4_single.lz4", "lz4")],
    )
    def test_frame_after_a_leading_skippable_frame_decides(
        self, temp_dir: Path, fixture: str, expected: str
    ) -> None:
        """Test that zstd and lz4, which share skippable frames, are told apart."""
        source = temp_dir / "data"  # No extension, so only the bytes can tell
        source.write_bytes(SKIPPABLE + (INTEROP / fixture).read_bytes())
        restored = temp_dir / "restored"

        assert _core.detect_format(str(source)) == expected
        _core.decompress_standalone(str(source), str(restored))
        assert restored.read_bytes() == PART1

    def test_v7_tar_is_detected_and_extracted(self, temp_dir: Path) -> None:
        """Test that GNU tar's --format=v7, which has no ustar magic, is a tar."""
        source = temp_dir / "data"
        source.write_bytes((INTEROP / "tar_v7.tar").read_bytes())

        assert _core.detect_format(str(source)) == "tar"
        _core.extract_archive(str(source), str(temp_dir), [])
        assert (temp_dir / "v7" / "file.txt").read_bytes() == PART1

    def test_uppercase_tar_gz_is_a_compressed_tar(self, temp_dir: Path) -> None:
        """Test that .TAR.GZ is read as tar in gzip, as .tar.gz is."""
        source = temp_dir / "A.TAR.GZ"
        source.write_bytes(gzip.compress((INTEROP / "tar_v7.tar").read_bytes()))

        _core.extract_archive(str(source), str(temp_dir), [])
        assert (temp_dir / "v7" / "file.txt").read_bytes() == PART1


class TestTarExtraction:
    """Test extracting tars made by GNU tar."""

    def test_hardlink_is_listed_with_its_target(self) -> None:
        """Test that GNU tar's link entry is reported as a hardlink, not special."""
        entries = _core.list_archive_contents(str(INTEROP / "tar_hardlink.tar"))

        link = next(e for e in entries if e["path"] == "hardlink/b.txt")
        assert link["type"] == "hardlink"
        assert link["link_target"] == "hardlink/a.txt"

    def test_hardlink_is_planned_as_a_hardlink(self, temp_dir: Path) -> None:
        """Test that the frontend's entries mark the link and keep its target."""
        plan = plan_extraction(INTEROP / "tar_hardlink.tar", temp_dir)

        links = [e for e in plan.entries if e.is_hardlink]
        assert [(e.path, e.link_target) for e in links] == [
            ("hardlink/b.txt", "hardlink/a.txt")
        ]

    def test_hardlink_extracts_as_a_link(self, temp_dir: Path) -> None:
        """Test that a hardlink entry becomes a second name for its target."""
        _core.extract_archive(str(INTEROP / "tar_hardlink.tar"), str(temp_dir), [])

        a, b = temp_dir / "hardlink" / "a.txt", temp_dir / "hardlink" / "b.txt"
        assert b.read_bytes() == PART1
        assert os.path.samefile(a, b)

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

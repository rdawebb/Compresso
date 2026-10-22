"""Tests for `compresso compress`."""

from __future__ import annotations

import gzip
import os
import tarfile
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

        result = invoke("decompress", archive, "-o", restored)
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
        invoke("decompress", archive, "-o", restored, "-q")

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
    """Test `compress` picks the job from its inputs and the format asked for."""

    def test_one_file_becomes_a_compresso_container(self, payload: Path) -> None:
        """Test that with no format, a single file gets the .comp container."""
        invoke("compress", payload, "-q")

        assert payload.with_suffix(payload.suffix + ".comp").exists()

    @pytest.mark.parametrize("fmt", ["gz", "bz2", "xz", "zst", "lz4", "sz"])
    def test_one_file_into_a_standalone_container(
        self, payload: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that -f names a single-file container, the suffix follows from it,
        and it comes back."""
        restored = tmp_path / "restored.bin"

        invoke("compress", payload, "-f", fmt, "-q")
        invoke("extract", f"{payload}.{fmt}", "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    def test_standalone_output_is_a_real_gzip(
        self, payload: Path, tmp_path: Path
    ) -> None:
        """Test that the .gz written is the format, not merely the name."""
        output = tmp_path / "out.gz"
        invoke("compress", payload, "-o", output, "-q")

        with gzip.open(output, "rb") as f:
            assert f.read() == payload.read_bytes()

    def test_a_directory_becomes_an_archive(self, source_tree: Path) -> None:
        """Test that a directory needs a container that holds many entries."""
        invoke("compress", source_tree, "-q")

        assert (source_tree.parent / f"{source_tree.name}.zip").exists()

    def test_several_files_become_an_archive(
        self, payload: Path, tmp_path: Path
    ) -> None:
        """Test that multiple files become an archive."""
        second = tmp_path / "second.bin"
        second.write_bytes(os.urandom(1024))
        output = tmp_path / "both.tar"

        invoke("compress", payload, second, "-o", output, "-q")

        with tarfile.open(output) as tar:
            assert sorted(tar.getnames()) == [payload.name, second.name]

    @pytest.mark.parametrize(
        "name,expected_magic",
        [
            ("out.gz", b"\x1f\x8b"),
            ("out.zst", b"\x28\xb5\x2f\xfd"),
            ("out.zip", b"PK"),
        ],
    )
    def test_format_inferred_from_the_output_name(
        self, payload: Path, tmp_path: Path, name: str, expected_magic: bytes
    ) -> None:
        """Test that a recognisable extension on -o chooses the format by itself."""
        output = tmp_path / name

        invoke("compress", payload, "-o", output, "-q")

        assert output.read_bytes().startswith(expected_magic)

    def test_explicit_format_beats_the_output_name(
        self, payload: Path, tmp_path: Path
    ) -> None:
        """Test that -f overrides whatever the extension suggests."""
        output = tmp_path / "misleading.gz"

        invoke("compress", payload, "-o", output, "-f", "zst", "-q")

        assert output.read_bytes().startswith(b"\x28\xb5\x2f\xfd")

    @pytest.mark.parametrize(
        "name", ["out.tar.zst", "out.tar.lz4", "out.tlz4", "out.tar.sz"]
    )
    def test_tar_inside_a_codec_is_an_archive(
        self, source_tree: Path, tmp_path: Path, name: str
    ) -> None:
        """Test that two-part extensions name an archive, not their codec alone."""
        output = tmp_path / name

        invoke("compress", source_tree, "-o", output, "-q")

        # Round-trips as an archive rather than as a lone compressed file
        dest = tmp_path / "back"
        invoke("extract", output, "-o", dest, "-q")
        assert (dest / source_tree.name).is_dir()

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

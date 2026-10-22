"""Tests for the frontend dispatch rules."""

import gzip
import os
import zipfile
from pathlib import Path

import pytest

from compresso.frontend.dispatch import (
    default_archive_output,
    format_is_single_file,
    infer_format,
    is_archive_format,
    looks_like_archive,
    resolve_single_output,
)


class TestInferFormat:
    """Test reading a format off an output filename."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("x.tar.lz4", "tar.lz4"),
            ("x.TAR.ZST", "tar.zst"),
            ("x.tar", "tar"),
            ("x.zip", "zip"),
            ("x.7z", "7z"),
            ("x.gz", "gz"),
            ("x.zst", "zst"),
            ("x.comp", None),
            ("x.txt", None),
            ("x", None),
            ("x.foo.lz4", "lz4"),
        ],
    )
    def test_suffix(self, name: str, expected: str | None) -> None:
        """Test that the longest known suffix wins, case-insensitively."""
        assert infer_format(Path(name)) == expected

    def test_no_output(self) -> None:
        """Test that no output path infers no format."""
        assert infer_format(None) is None


class TestFormatKinds:
    """Test classifying a format as an archive or a single-file container."""

    @pytest.mark.parametrize(
        ("fmt", "archive", "single"),
        [
            ("zip", True, False),
            ("TAR.GZ", True, False),
            ("7z", True, False),
            ("gz", False, True),
            ("ZSTD", False, True),
            ("comp", False, False),
            ("nope", False, False),
            (None, False, False),
        ],
    )
    def test_kind(self, fmt: str | None, archive: bool, single: bool) -> None:
        """Test that `.comp` and unknown formats are neither kind."""
        assert is_archive_format(fmt) is archive
        assert format_is_single_file(fmt) is single


class TestLooksLikeArchive:
    """Test detecting an archive from its bytes rather than its name."""

    def test_zip_under_any_name(self, tmp_path: Path) -> None:
        """Test that a zip is recognised without a `.zip` suffix."""
        path = tmp_path / "data.bin"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.txt", "a")

        assert looks_like_archive(path)

    def test_single_file_container(self, tmp_path: Path) -> None:
        """Test that a gzip stream is not an archive, even named `.zip`."""
        path = tmp_path / "data.zip"
        path.write_bytes(gzip.compress(b"a"))

        assert not looks_like_archive(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test that a missing file is not an archive rather than an error."""
        assert not looks_like_archive(tmp_path / "missing.zip")


class TestDefaultArchiveOutput:
    """Test naming an archive the caller did not name."""

    def test_beside_the_input(self, tmp_path: Path) -> None:
        """Test that the archive is named after the input, next to it."""
        src = tmp_path / "src"
        src.mkdir()

        assert default_archive_output(src, "tar.zst") == tmp_path / "src.tar.zst"

    @pytest.mark.parametrize("name", [".", f"..{os.sep}src{os.sep}"])
    def test_trailing_separator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        """Test that `.` and a trailing separator still name the directory."""
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.chdir(src)

        assert (
            default_archive_output(Path(name), "zip") == tmp_path.resolve() / "src.zip"
        )


class TestResolveSingleOutput:
    """Test where `-o` sends a single-file job."""

    def test_no_output_keeps_default(self, tmp_path: Path) -> None:
        """Test that without `-o` the default path is used."""
        default = tmp_path / "x.gz"

        assert resolve_single_output(None, default) == default

    def test_existing_directory_receives_the_file(self, tmp_path: Path) -> None:
        """Test that an existing directory gets the default filename inside it."""
        out = tmp_path / "out"
        out.mkdir()

        assert resolve_single_output(out, tmp_path / "x.gz") == out / "x.gz"

    def test_anything_else_is_the_file(self, tmp_path: Path) -> None:
        """Test that any other path is taken as the output file itself."""
        out = tmp_path / "out" / "y.gz"

        assert resolve_single_output(out, tmp_path / "x.gz") == out

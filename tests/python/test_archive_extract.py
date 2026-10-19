"""Tests for listing and extracting archives."""

import gzip
import io
import logging
import os
import stat
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from compresso import (
    BackendError,
    CorruptDataError,
    ExtractionPolicyError,
    IncompleteExtractionError,
    _core,
)

from .helpers import (
    dir_entry,
    file_entry,
    link_entry,
    make_tar,
    node_entry,
    umask,
)


class TestListArchiveContentsArgs:
    """Test the argument handling of list_archive_contents."""

    def test_keyword_arguments_are_refused(self, tmp_path: Path) -> None:
        """Test that a stray keyword raises rather than being silently dropped."""
        with pytest.raises(TypeError, match="keyword"):
            _core.list_archive_contents(str(tmp_path / "a.tar"), bogus=1)  # type: ignore[call-arg]  # ty:ignore[unknown-argument]


class TestExtractArchiveFilesArg:
    """Test the validation of extract_archive's `files` selection."""

    @pytest.fixture
    def two_files(self, tmp_path: Path) -> Path:
        """A tar holding a.txt and b.txt.

        Args:
            tmp_path: Pytest temporary path fixture.

        Returns:
            Path to the tar.
        """
        archive = tmp_path / "two.tar"
        make_tar(archive, file_entry("a.txt"), file_entry("b.txt"))

        return archive

    @pytest.mark.parametrize(
        "files",
        [["a.txt"], ("a.txt",), iter(["a.txt"])],
        ids=["list", "tuple", "iterator"],
    )
    def test_any_sequence_selects(
        self, two_files: Path, tmp_path: Path, files: object
    ) -> None:
        """Test that a tuple or iterator selects like a list, not as "everything"."""
        out = tmp_path / "out"

        _core.extract_archive(str(two_files), str(out), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

        assert sorted(p.name for p in out.iterdir()) == ["a.txt"]

    def test_empty_selects_everything(self, two_files: Path, tmp_path: Path) -> None:
        """Test that no names at all extracts every entry."""
        out = tmp_path / "out"

        _core.extract_archive(str(two_files), str(out), ())

        assert sorted(p.name for p in out.iterdir()) == ["a.txt", "b.txt"]

    @pytest.mark.parametrize("files", [["a.txt", 1], [None]], ids=["int", "none"])
    def test_non_str_name_is_refused(
        self, two_files: Path, tmp_path: Path, files: list[object]
    ) -> None:
        """Test that a name that isn't a str raises rather than crashing."""
        with pytest.raises(TypeError, match="files must contain only str"):
            _core.extract_archive(str(two_files), str(tmp_path / "out"), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    @pytest.mark.parametrize("files", ["a.txt", b"a.txt"], ids=["str", "bytes"])
    def test_bare_string_is_refused(
        self, two_files: Path, tmp_path: Path, files: object
    ) -> None:
        """Test that one name passed bare isn't read as a name per character."""
        with pytest.raises(TypeError, match="files must be a sequence of str"):
            _core.extract_archive(str(two_files), str(tmp_path / "out"), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


class TestSelectByPrefix:
    """Test that a directory's name selects everything below it, as tar does."""

    NAMES = ("d/", "d/x.txt", "d/sub/", "d/sub/y.txt", "dx.txt", "e.txt")

    @pytest.fixture(params=["tar", "zip"])
    def archive(self, request: pytest.FixtureRequest, tmp_path: Path) -> Path:
        """A tar or zip holding NAMES.

        Args:
            request: Pytest request, carrying the format.
            tmp_path: Pytest temporary path fixture.

        Returns:
            Path to the archive.
        """
        path = tmp_path / f"sel.{request.param}"
        if request.param == "tar":
            make_tar(
                path,
                *(
                    dir_entry(name.rstrip("/"))
                    if name.endswith("/")
                    else file_entry(name, mode=0o755)
                    for name in self.NAMES
                ),
            )

        else:
            with zipfile.ZipFile(path, "w") as zf:
                for name in self.NAMES:
                    zf.writestr(name, "" if name.endswith("/") else "x")

        return path

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            (["d"], ["d/sub/y.txt", "d/x.txt"]),
            (["d/"], ["d/sub/y.txt", "d/x.txt"]),
            (["d/sub"], ["d/sub/y.txt"]),
            (["e.txt", "d/sub/y.txt"], ["d/sub/y.txt", "e.txt"]),
            (["d", "d/x.txt", "d"], ["d/sub/y.txt", "d/x.txt"]),
        ],
        ids=["dir", "trailing-slash", "nested", "files", "overlapping"],
    )
    def test_selects_at_component_boundaries(
        self, archive: Path, tmp_path: Path, files: list[str], expected: list[str]
    ) -> None:
        """Test that a name matches whole components, never part of one."""
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), files)

        extracted = [p.relative_to(out).as_posix() for p in out.rglob("*")]
        assert sorted(p for p in extracted if (out / p).is_file()) == expected

    def test_unmatched_names_are_reported_after_the_rest(
        self, archive: Path, tmp_path: Path
    ) -> None:
        """Test that names matching nothing raise, keeping what was found, as bsdtar does."""
        out = tmp_path / "out"

        with pytest.raises(
            IncompleteExtractionError, match=r"Not found in archive: d/x, nope$"
        ):
            _core.extract_archive(str(archive), str(out), ["nope", "e.txt", "d/x"])

        assert [p.name for p in out.iterdir()] == ["e.txt"]


class TestRecognisedButUnsupportedArchive:
    """Test that a detected archive format without a backend names itself."""

    @pytest.fixture
    def fake_7z(self, tmp_path: Path) -> Path:
        """A file that detects as 7z by its magic bytes alone.

        Args:
            tmp_path: Pytest temporary path fixture.

        Returns:
            Path to the fake 7z file.
        """
        path = tmp_path / "fake.7z"
        path.write_bytes(b"7z\xbc\xaf\x27\x1c" + bytes(32))

        return path

    def test_listing_names_the_format(self, fake_7z: Path) -> None:
        """Test that listing reports 7z rather than a generic failure."""
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.list_archive_contents(str(fake_7z))

    def test_extracting_names_the_format(self, fake_7z: Path, tmp_path: Path) -> None:
        """Test that extraction reports 7z and writes nothing."""
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.extract_archive(str(fake_7z), str(out), [])
        assert list(out.iterdir()) == []

    def test_creating_names_the_format(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that creation reports 7z and leaves no output behind."""
        dest = tmp_path / "new.7z"
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.create_archive(str(dest), "7z", [str(sample_text_file)])
        assert not dest.exists()


# Random data, so a compressed copy is about as long as the tar
_TWO_ENTRIES = (file_entry("a.bin", 5000), file_entry("b.bin", 5000))


class TestCompressedTarListing:
    """Test listing a compressed tar, which decodes it as libarchive reads."""

    def _tar_gz(self, tmp_path: Path) -> bytearray:
        """Gzip a two-entry tar, padded past its end-of-archive records.

        Args:
            tmp_path: Pytest temporary path fixture.

        Returns:
            The compressed archive as a bytearray.
        """
        return bytearray(
            gzip.compress(
                make_tar(tmp_path / "plain.tar", *_TWO_ENTRIES, fill=os.urandom)
            )
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
    def test_lists_from_a_read_only_directory(self, tmp_path: Path) -> None:
        """Test that listing writes nothing beside the archive."""
        read_only = tmp_path / "ro"
        read_only.mkdir()
        archive = read_only / "a.tar.gz"
        archive.write_bytes(self._tar_gz(tmp_path))
        read_only.chmod(0o555)

        entries = _core.list_archive_contents(str(archive))

        assert [e["path"] for e in entries] == ["a.bin", "b.bin"]

    def test_checksum_after_the_last_entry_is_still_checked(
        self, tmp_path: Path
    ) -> None:
        """Test that the stream is read to its end, past the tar's end records."""
        damaged = self._tar_gz(tmp_path)
        damaged[-8] ^= 0xFF  # The CRC-32 in the gzip trailer
        archive = tmp_path / "damaged.tar.gz"
        archive.write_bytes(damaged)

        with pytest.raises(CorruptDataError, match="incorrect data check"):
            _core.list_archive_contents(str(archive))

    def test_damaged_stream_reports_the_codec_error(self, tmp_path: Path) -> None:
        """Test that the codec's error wins over libarchive's failed read."""
        damaged = self._tar_gz(tmp_path)
        archive = tmp_path / "truncated.tar.gz"
        archive.write_bytes(damaged[:600])

        with pytest.raises(CorruptDataError, match="Truncated or incomplete gzip"):
            _core.list_archive_contents(str(archive))


class TestExtractionUndo:
    """Test that a failed extraction leaves the output as it found it."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
    def test_compressed_tar_extracts_from_a_read_only_directory(
        self, tmp_path: Path
    ) -> None:
        """Test that the archive is decoded as it is read, not into a temp."""
        read_only = tmp_path / "ro"
        read_only.mkdir()
        archive = read_only / "a.tar.gz"
        archive.write_bytes(
            gzip.compress(
                make_tar(tmp_path / "plain.tar", *_TWO_ENTRIES, fill=os.urandom)
            )
        )
        read_only.chmod(0o555)
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert sorted(p.name for p in out.iterdir()) == ["a.bin", "b.bin"]

    def test_damaged_checksum_undoes_the_entries(self, tmp_path: Path) -> None:
        """Test that a failure found only after the last entry removes them all."""
        # Padding, so the trailer is only decoded after both entries are written
        tar = make_tar(tmp_path / "plain.tar", *_TWO_ENTRIES, fill=os.urandom) + bytes(
            200_000
        )
        damaged = bytearray(gzip.compress(tar))
        damaged[-8] ^= 0xFF  # The CRC-32 in the gzip trailer
        archive = tmp_path / "damaged.tar.gz"
        archive.write_bytes(damaged)
        out = tmp_path / "out"

        with pytest.raises(CorruptDataError, match="incorrect data check"):
            _core.extract_archive(str(archive), str(out), [])

        assert list(out.iterdir()) == []

    def test_refused_entry_restores_what_overwrite_replaced(
        self, tmp_path: Path
    ) -> None:
        """Test that the file OVERWRITE moved aside is put back."""
        plain = tmp_path / "plain.tar"
        make_tar(
            plain,
            file_entry("d/a.txt", 3),
            link_entry("d/link", "a.txt", kind=tarfile.SYMTYPE),
            fill=lambda n: b"new",
        )
        archive = tmp_path / "a.tar.zst"
        _core.compress_standalone(str(plain), str(archive), "zst")
        out = tmp_path / "out"
        (out / "d").mkdir(parents=True)
        (out / "d" / "a.txt").write_text("original")

        with pytest.raises(ExtractionPolicyError, match="symlink"):
            _core.extract_archive(str(archive), str(out), [], overwrite=2)

        assert os.listdir(out / "d") == ["a.txt"]
        assert (out / "d" / "a.txt").read_text() == "original"

    def test_overwrite_drops_the_originals_on_success(self, tmp_path: Path) -> None:
        """Test that no moved-aside original outlives a finished extraction."""
        archive = tmp_path / "a.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d/a"))
        out = tmp_path / "out"
        (out / "d").mkdir(parents=True)
        (out / "d" / "a").write_text("old a")
        (out / "d" / "b").write_text("old b")

        _core.extract_archive(str(archive), str(out), [], overwrite=2)

        assert sorted(os.listdir(out / "d")) == ["a", "b"]
        assert (out / "d" / "b").read_text() == "x"

    def test_failure_after_validation_undoes_a_plain_tar(self, tmp_path: Path) -> None:
        """Test that what only fails while writing is undone, directories too."""
        archive = tmp_path / "links.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d"))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="not a file"):
            _core.extract_archive(str(archive), str(out), [])

        assert list(out.iterdir()) == []


class TestArchiveErrorTypes:
    """Test that archive failures say whether the input, the OS or policy failed."""

    def test_damaged_later_tar_header_is_corrupt_data(self, tmp_path: Path) -> None:
        """Test that a bad checksum on the second header is corrupt input."""
        damaged = bytearray(
            make_tar(tmp_path / "good.tar", *_TWO_ENTRIES, fill=os.urandom)
        )
        damaged[512 + 5120 + 100] ^= 0xFF
        archive = tmp_path / "damaged.tar"
        archive.write_bytes(damaged)

        with pytest.raises(CorruptDataError, match="Damaged tar archive"):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [])

    def test_truncated_tar_is_corrupt_data(self, tmp_path: Path) -> None:
        """Test that a tar cut off inside an entry's data is corrupt input."""
        archive = tmp_path / "truncated.tar"
        archive.write_bytes(
            make_tar(tmp_path / "good.tar", *_TWO_ENTRIES, fill=os.urandom)[:3000]
        )

        with pytest.raises(CorruptDataError, match="Truncated"):
            _core.list_archive_contents(str(archive))

    def test_damaged_zip_data_is_corrupt_data(self, tmp_path: Path) -> None:
        """Test that a zip entry whose deflate stream is damaged is corrupt input."""
        good = tmp_path / "good.zip"
        with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", b"hello world " * 500)
        damaged = bytearray(good.read_bytes())
        damaged[60] ^= 0xFF  # Inside the first entry's compressed data
        archive = tmp_path / "damaged.zip"
        archive.write_bytes(damaged)

        with pytest.raises(CorruptDataError):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [])

    def test_truncated_zip_is_corrupt_data(self, tmp_path: Path) -> None:
        """Test that a zip missing the end of its central directory is corrupt."""
        good = tmp_path / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("a.txt", b"hello")
        archive = tmp_path / "truncated.zip"
        archive.write_bytes(good.read_bytes()[:-30])

        with pytest.raises(CorruptDataError):
            _core.list_archive_contents(str(archive))

    @pytest.mark.parametrize("call", ["extract", "list"])
    def test_missing_archive_is_file_not_found(self, tmp_path: Path, call: str) -> None:
        """Test that a missing archive is an OS error, not an unknown format."""
        missing = str(tmp_path / "missing.tar")

        with pytest.raises(FileNotFoundError):
            if call == "extract":
                _core.extract_archive(missing, str(tmp_path / "out"), [])
            else:
                _core.list_archive_contents(missing)

    @pytest.mark.parametrize("call", ["extract", "list"])
    def test_directory_archive_is_unreadable(self, tmp_path: Path, call: str) -> None:
        """Test that a directory is refused as unreadable, not as unrecognised."""
        # Windows refuses to open a directory at all, so it reports EACCES
        with pytest.raises((IsADirectoryError, PermissionError)):
            if call == "extract":
                _core.extract_archive(str(tmp_path), str(tmp_path / "out"), [])
            else:
                _core.list_archive_contents(str(tmp_path))

    def test_missing_output_dir_parents_are_created(self, tmp_path: Path) -> None:
        """Test that every missing parent is made, whichever separator the path uses.

        Also includes str() of a Windows path, which uses backslashes.
        """
        archive = tmp_path / "x.tar"
        make_tar(archive, file_entry("f"))
        out = tmp_path / "a" / "b" / "c"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "f").read_bytes() == b"x"

    def test_output_dir_that_is_a_file_is_not_a_directory(self, tmp_path: Path) -> None:
        """Test that the output path itself is named, before any entry is tried."""
        archive = tmp_path / "x.tar"
        make_tar(archive, file_entry("f"))
        not_a_dir = tmp_path / "afile"
        not_a_dir.write_text("x")

        with pytest.raises(NotADirectoryError) as info:
            _core.extract_archive(str(archive), str(not_a_dir), [])

        assert info.value.filename == str(not_a_dir)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
    def test_output_dir_that_cannot_be_created_says_why(self, tmp_path: Path) -> None:
        """Test that a read-only parent is a permission error, not a missing path."""
        archive = tmp_path / "x.tar"
        make_tar(archive, file_entry("f"))
        read_only = tmp_path / "ro"
        read_only.mkdir(mode=0o555)

        with pytest.raises(PermissionError) as info:
            _core.extract_archive(str(archive), str(read_only / "out"), [])

        assert info.value.filename == str(read_only / "out")


class TestSpecialExtraction:
    """Test that FIFOs and devices are created as tar creates them."""

    def test_listing_names_the_kind(self, tmp_path: Path) -> None:
        """Test that each node is listed by its kind rather than as special."""
        archive = tmp_path / "nodes.tar"
        make_tar(
            archive,
            file_entry("a"),
            node_entry("fifo", tarfile.FIFOTYPE),
            node_entry("char", tarfile.CHRTYPE),
            node_entry("block", tarfile.BLKTYPE),
        )

        entries = _core.list_archive_contents(str(archive))

        assert [(e["path"], e["type"]) for e in entries] == [
            ("a", "file"),
            ("fifo", "fifo"),
            ("char", "char"),
            ("block", "block"),
        ]

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no FIFOs")
    def test_fifo_is_created_with_its_metadata(self, tmp_path: Path) -> None:
        """Test that a FIFO is made, with the stored mode under the umask, and mtime."""
        archive = tmp_path / "nodes.tar"
        make_tar(
            archive,
            file_entry("a"),
            node_entry("fifo", tarfile.FIFOTYPE, 0o640, 1_000_000_000),
        )

        _core.extract_archive(str(archive), str(tmp_path / "out"), [])

        st = os.lstat(tmp_path / "out" / "fifo")
        assert stat.S_ISFIFO(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o640 & ~umask()
        assert st.st_mtime == 1_000_000_000

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX, not root"
    )
    def test_device_is_skipped_without_root(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that a device is skipped with a warning, the rest still extracted."""
        archive = tmp_path / "nodes.tar"
        make_tar(archive, file_entry("a"), node_entry("char", tarfile.CHRTYPE))
        out = tmp_path / "out"

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.extract_archive(str(archive), str(out), [])

        assert os.listdir(out) == ["a"]
        assert "Skipped char: devices are only created by root" in caplog.text

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() != 0, reason="needs root"
    )
    def test_device_is_created_as_root(self, tmp_path: Path) -> None:
        """Test that a device is made with its stored number."""
        archive = tmp_path / "nodes.tar"
        make_tar(archive, file_entry("a"), node_entry("char", tarfile.CHRTYPE))

        _core.extract_archive(str(archive), str(tmp_path / "out"), [])

        st = os.lstat(tmp_path / "out" / "char")
        assert stat.S_ISCHR(st.st_mode)
        assert st.st_rdev == os.makedev(1, 3)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_windows_skips_a_fifo(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that a FIFO is skipped with a warning, the rest still extracted."""
        archive = tmp_path / "nodes.tar"
        make_tar(archive, file_entry("a"), node_entry("fifo", tarfile.FIFOTYPE))
        out = tmp_path / "out"

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.extract_archive(str(archive), str(out), [])

        assert os.listdir(out) == ["a"]
        assert "Skipped fifo: FIFOs can't be created on this platform" in caplog.text


class TestDirectoryMetadata:
    """Test that a directory's mode and mtime are applied after its contents."""

    MTIME = 1577836800  # 2020-01-01T00:00:00Z

    def test_directory_mtime_is_restored(self, tmp_path: Path) -> None:
        """Test that writing a directory's files doesn't leave it with today's mtime."""
        archive = tmp_path / "dated.tar"
        make_tar(
            archive,
            dir_entry("d", mtime=self.MTIME),
            file_entry("d/f", mtime=self.MTIME),
        )

        _core.extract_archive(str(archive), str(tmp_path / "out"), [])

        assert int((tmp_path / "out" / "d").stat().st_mtime) == self.MTIME

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_nested_read_only_directories_are_filled(self, tmp_path: Path) -> None:
        """Test that read-only directories, nested, all get their files and modes."""
        archive = tmp_path / "readonly.tar"
        make_tar(
            archive,
            dir_entry("a", 0o555, self.MTIME),
            dir_entry("a/b", 0o555, self.MTIME),
            file_entry("a/b/f", mtime=self.MTIME),
            file_entry("a/g", mtime=self.MTIME),
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "a" / "b" / "f").read_bytes() == b"x"
        assert (out / "a" / "g").read_bytes() == b"x"
        for directory in (out / "a", out / "a" / "b"):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o555
            assert int(directory.stat().st_mtime) == self.MTIME

    def test_existing_directory_keeps_its_metadata(self, tmp_path: Path) -> None:
        """Test that merging into a directory that already exists leaves it as is."""
        archive = tmp_path / "dated.tar"
        make_tar(
            archive,
            dir_entry("d", 0o700, self.MTIME),
            file_entry("d/f", mtime=self.MTIME),
        )
        existing = tmp_path / "out" / "d"
        existing.mkdir(parents=True)
        before = existing.stat()

        _core.extract_archive(str(archive), str(tmp_path / "out"), [])

        assert existing.stat().st_mode == before.st_mode
        assert int(existing.stat().st_mtime) != self.MTIME

    def test_opt_in_restores_an_existing_directory(self, tmp_path: Path) -> None:
        """Test that overwrite_dir_metadata applies GNU tar's behaviour."""
        archive = tmp_path / "dated.tar"
        make_tar(
            archive,
            dir_entry("d", 0o700, self.MTIME),
            file_entry("d/f", mtime=self.MTIME),
        )
        existing = tmp_path / "out" / "d"
        existing.mkdir(parents=True)

        _core.extract_archive(
            str(archive), str(tmp_path / "out"), [], overwrite_dir_metadata=True
        )

        assert int(existing.stat().st_mtime) == self.MTIME
        if sys.platform != "win32":
            assert stat.S_IMODE(existing.stat().st_mode) == 0o700

    def test_opt_in_covers_a_directory_listed_after_its_contents(
        self, tmp_path: Path
    ) -> None:
        """Test that a directory created as a parent still gets its own entry's metadata."""
        archive = tmp_path / "late.tar"
        make_tar(
            archive,
            file_entry("d/f", mtime=self.MTIME),
            dir_entry("d", mtime=self.MTIME),
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], overwrite_dir_metadata=True)

        assert int((out / "d").stat().st_mtime) == self.MTIME

    def test_opt_in_never_touches_an_existing_directory_when_skipping(
        self, tmp_path: Path
    ) -> None:
        """Test that SKIP mode keeps its promise to leave existing paths alone."""
        archive = tmp_path / "dated.tar"
        make_tar(
            archive,
            dir_entry("d", 0o700, self.MTIME),
            file_entry("d/f", mtime=self.MTIME),
        )
        existing = tmp_path / "out" / "d"
        existing.mkdir(parents=True)
        before = existing.stat()

        _core.extract_archive(
            str(archive),
            str(tmp_path / "out"),
            [],
            overwrite=1,
            overwrite_dir_metadata=True,
        )

        assert existing.stat().st_mode == before.st_mode
        assert int(existing.stat().st_mtime) != self.MTIME


def _tar_with_non_utf8_pax_name(path: Path) -> None:
    """Write a pax tar whose one entry name holds a byte that isn't UTF-8.

    libarchive returns ARCHIVE_WARN for its header: still valid, but the name
    can't be converted to the locale's charset.

    Args:
        path: Where to write the tar.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        # Too long for a ustar header, so the name goes in a pax "path" record
        info = tarfile.TarInfo("dir/" + "n" * 120 + ".txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))

    data = bytearray(buf.getvalue())
    # Same length, so the record stays well-formed
    data[data.index(b"path=dir/") + len(b"path=dir/")] = 0xFF
    path.write_bytes(data)


@pytest.mark.skipif(
    sys.platform == "win32", reason="libarchive converts names to UTF-16 there"
)
class TestTarHeaderWarnings:
    """Test that a header libarchive only warns about is still read."""

    def test_listing_warns_and_keeps_the_entry(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that the entry is listed, its name kept by surrogateescape."""
        archive = tmp_path / "warn.tar"
        _tar_with_non_utf8_pax_name(archive)

        with caplog.at_level(logging.WARNING, logger="compresso"):
            entries = _core.list_archive_contents(str(archive))

        assert [e["path"] for e in entries] == ["dir/\udcff" + "n" * 119 + ".txt"]
        assert len(caplog.records) == 1
        # Shown as \xff, since a lone surrogate breaks handlers writing UTF-8
        message = caplog.records[0].getMessage()
        assert message.startswith("Archive entry dir/\\xffnnn")
        assert "can't be converted" in message

    # macOS refuses a file name that isn't UTF-8, so only Linux can write it
    @pytest.mark.skipif(sys.platform != "linux", reason="needs non-UTF-8 names")
    def test_extraction_warns_once_and_writes_the_entry(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that extraction carries on, warning once despite two passes."""
        archive = tmp_path / "warn.tar"
        _tar_with_non_utf8_pax_name(archive)
        out = tmp_path / "out"

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.extract_archive(str(archive), str(out), [])

        written = os.listdir(os.fsencode(out / "dir"))
        assert written == [b"\xff" + b"n" * 119 + b".txt"]
        assert len(caplog.records) == 1


class TestHardlinkExtraction:
    """Test that a hardlink's target is held to the rules of an entry's path."""

    @pytest.mark.parametrize("target", ["../outside", "/etc/passwd"])
    def test_target_outside_the_root_is_refused(
        self, tmp_path: Path, target: str
    ) -> None:
        """Test that the whole archive is refused before anything is written."""
        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", target))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError):
            _core.extract_archive(str(archive), str(out), [])

        assert not (out / "d").exists()

    def test_unselected_target_is_reported(self, tmp_path: Path) -> None:
        """Test that a link whose target wasn't extracted names the missing file."""
        archive = tmp_path / "links.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d/a"))
        out = tmp_path / "out"

        with pytest.raises(
            IncompleteExtractionError, match=r"^Hardlink target missing: d/b -> d/a$"
        ):
            _core.extract_archive(str(archive), str(out), ["d/b"])

        assert list(out.iterdir()) == []

    def test_missing_target_is_skipped_keeping_the_rest(self, tmp_path: Path) -> None:
        """Test that the link is reported with any unmatched name, as tar does."""
        archive = tmp_path / "links.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d/missing"))
        out = tmp_path / "out"

        with pytest.raises(
            IncompleteExtractionError,
            match=r"^Not found in archive: nope; "
            r"Hardlink target missing: d/b -> d/missing$",
        ):
            _core.extract_archive(str(archive), str(out), ["d", "nope"])

        assert os.listdir(out / "d") == ["a"]

    @pytest.mark.usefixtures("needs_symlinks")
    def test_target_replaced_by_a_symlink_is_refused(self, tmp_path: Path) -> None:
        """Test that a link is never made to whatever a planted symlink points at."""
        archive = tmp_path / "links.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d/s"))
        out = tmp_path / "out"
        (out / "d").mkdir(parents=True)
        (tmp_path / "secret").write_text("secret")
        (out / "d" / "s").symlink_to(tmp_path / "secret")

        with pytest.raises(ExtractionPolicyError, match="not a file, FIFO or device"):
            _core.extract_archive(str(archive), str(out), [], overwrite=2)

    def test_existing_link_path_follows_the_overwrite_mode(
        self, tmp_path: Path
    ) -> None:
        """Test ERROR, SKIP, OVERWRITE and RENAME against an existing d/b."""
        archive = tmp_path / "links.tar"
        make_tar(archive, file_entry("d/a"), link_entry("d/b", "d/a"))

        def extract(mode: int) -> Path:
            out = tmp_path / f"out{mode}"
            (out / "d").mkdir(parents=True)
            (out / "d" / "b").write_text("existing")
            _core.extract_archive(str(archive), str(out), [], overwrite=mode)
            return out / "d"

        with pytest.raises(FileExistsError):
            extract(0)

        skipped = extract(1)
        assert (skipped / "b").read_text() == "existing"

        replaced = extract(2)
        assert os.path.samefile(replaced / "a", replaced / "b")

        renamed = extract(3)
        assert (renamed / "b").read_text() == "existing"
        [extra] = [p for p in renamed.iterdir() if p.name not in ("a", "b")]
        assert os.path.samefile(renamed / "a", extra)


class TestSymlinksAsFiles:
    """Test allow_symlinks=2, which writes a symlink's target as a regular file."""

    def test_link_becomes_a_file_holding_its_target(self, tmp_path: Path) -> None:
        """Test the file's content, and that the link's 0777 mode isn't applied."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive, file_entry("d/a"), link_entry("d/b", "a", kind=tarfile.SYMTYPE)
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], allow_symlinks=2)

        link = out / "d" / "b"
        assert not link.is_symlink()
        assert link.read_bytes() == b"a"
        if sys.platform != "win32":
            assert link.stat().st_mode & 0o111 == 0

    def test_target_text_counts_toward_the_size_cap(self, tmp_path: Path) -> None:
        """Test that the cap covers the written target, and nothing is left."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive, file_entry("d/a"), link_entry("d/b", "a", kind=tarfile.SYMTYPE)
        )
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="maximum extracted size"):
            _core.extract_archive(
                str(archive), str(out), [], allow_symlinks=2, max_total_size=1
            )

        assert list(out.iterdir()) == []


class TestSymlinkExtraction:
    """Test allow_symlinks=1, which creates links whose targets stay inside."""

    @pytest.mark.usefixtures("needs_symlinks")
    def test_link_is_created_as_stored(self, tmp_path: Path) -> None:
        """Test that the link holds its target verbatim, and resolves to it."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive, file_entry("d/a"), link_entry("d/b", "a", kind=tarfile.SYMTYPE)
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        link = out / "d" / "b"
        assert link.is_symlink()
        assert os.readlink(link) == "a"
        assert link.read_bytes() == b"x"

    @pytest.mark.parametrize(
        "target", ["../../x", "/etc/passwd", "C:x", "a/../a", "../d/../a", ""]
    )
    def test_target_that_may_escape_is_refused(
        self, tmp_path: Path, target: str
    ) -> None:
        """Test escapes, absolute targets and a ".." after a name, before any write."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive, file_entry("d/a"), link_entry("d/b", target, kind=tarfile.SYMTYPE)
        )
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="symlink|Symlink"):
            _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        assert list(out.iterdir()) == []

    @pytest.mark.usefixtures("needs_symlinks")
    def test_target_counts_from_where_the_link_lands(self, tmp_path: Path) -> None:
        """Test that a link under an extracted `s -> .` can't climb out.

        `s/l -> ../x` looks contained, but `s` is the root itself, so the
        target resolves outside it.
        """
        archive = tmp_path / "links.tar"
        make_tar(
            archive,
            link_entry("s", ".", kind=tarfile.SYMTYPE),
            link_entry("s/l", "../x", kind=tarfile.SYMTYPE),
        )
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="symlink target"):
            _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        assert list(out.iterdir()) == []

    @pytest.mark.usefixtures("needs_symlinks")
    def test_link_gets_its_own_mtime(self, tmp_path: Path) -> None:
        """Test that the stored mtime lands on the link, not on its target."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive,
            file_entry("d/a"),
            link_entry("d/b", "a", kind=tarfile.SYMTYPE, mtime=1_000_000_000),
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        assert (out / "d" / "b").lstat().st_mtime == 1_000_000_000
        assert (out / "d" / "a").stat().st_mtime != 1_000_000_000


class TestHardlinkToSymlink:
    """Test a hardlink naming an extracted symlink, which links the symlink."""

    @pytest.mark.usefixtures("needs_symlinks")
    def test_symlink_itself_is_linked(self, tmp_path: Path) -> None:
        """Test that the link is the symlink's inode, not its target's."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive,
            link_entry("d/s", "../x", kind=tarfile.SYMTYPE),
            link_entry("d/b", "d/s"),
        )
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        link = out / "d" / "b"
        assert Path(os.readlink(link)) == Path("../x")
        assert link.lstat().st_ino == (out / "d" / "s").lstat().st_ino

    @pytest.mark.usefixtures("needs_symlinks")
    def test_target_is_checked_from_where_the_link_lands(self, tmp_path: Path) -> None:
        """Test that `../x`, inside from d/, is refused for a link at the root."""
        archive = tmp_path / "links.tar"
        make_tar(
            archive,
            link_entry("d/s", "../x", kind=tarfile.SYMTYPE),
            link_entry("b", "d/s"),
        )
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="symlink target"):
            _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        assert list(out.iterdir()) == []


def _zip_with_unix_modes(path: Path) -> None:
    """Write a zip holding a 0750 file, a 0700 directory, a link and a DOS file.

    Args:
        path: Where to write the zip.
    """
    with zipfile.ZipFile(path, "w") as zf:
        for name, unix_mode, data in (
            ("d/", 0o040700, b""),
            ("d/run", 0o100750, b"x"),
            ("d/link", 0o120777, b"run"),
            ("dos", 0, b"y"),
        ):
            info = zipfile.ZipInfo(name)
            info.create_system = 3 if unix_mode else 0
            info.external_attr = unix_mode << 16
            zf.writestr(info, data)


class TestZipModesAndSymlinks:
    """Test that zip keeps Unix modes and symlinks in its external attributes."""

    @pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX modes")
    def test_modes_are_restored(self, tmp_path: Path) -> None:
        """Test the stored modes, and the usual default for a DOS entry."""
        archive = tmp_path / "a.zip"
        _zip_with_unix_modes(archive)
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], exact_permissions=True)

        assert stat.S_IMODE((out / "d").stat().st_mode) == 0o700
        assert stat.S_IMODE((out / "d" / "run").stat().st_mode) == 0o750
        assert stat.S_IMODE((out / "dos").stat().st_mode) == 0o644

    def test_link_is_listed_with_its_target(self, tmp_path: Path) -> None:
        """Test that listing reports the link's type and target, not its data."""
        archive = tmp_path / "a.zip"
        _zip_with_unix_modes(archive)

        entries = {e["path"]: e for e in _core.list_archive_contents(str(archive))}

        assert entries["d/link"]["type"] == "symlink"
        assert entries["d/link"]["link_target"] == "run"

    def test_link_becomes_a_file_by_default(self, tmp_path: Path) -> None:
        """Test that zip's default is mode 2, as before links were recognised."""
        archive = tmp_path / "a.zip"
        _zip_with_unix_modes(archive)
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert not (out / "d" / "link").is_symlink()
        assert (out / "d" / "link").read_bytes() == b"run"

    @pytest.mark.usefixtures("needs_symlinks")
    def test_link_is_created_on_request(self, tmp_path: Path) -> None:
        """Test that allow_symlinks=1 creates the link itself."""
        archive = tmp_path / "a.zip"
        _zip_with_unix_modes(archive)
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], allow_symlinks=1)

        assert os.readlink(out / "d" / "link") == "run"
        assert (out / "d" / "link").read_bytes() == b"x"


@pytest.mark.usefixtures("needs_symlinks")
class TestOverwriteReplacesSymlinks:
    """Test that OVERWRITE replaces a symlink at an entry's path."""

    def test_symlink_at_the_entry_path_is_not_followed(self, tmp_path: Path) -> None:
        """Test that the entry's data never lands where the link points."""
        archive = tmp_path / "a.tar"
        make_tar(archive, file_entry("a.txt", mtime=1_000_000_000))
        outside = tmp_path / "outside.txt"
        outside.write_text("keep me")
        out = tmp_path / "out"
        out.mkdir()
        (out / "a.txt").symlink_to(outside)

        _core.extract_archive(str(archive), str(out), [], overwrite=2)

        assert outside.read_text() == "keep me"
        assert not (out / "a.txt").is_symlink()
        assert (out / "a.txt").read_bytes() == b"x"


class TestExtractionRefusesDeniedEntryTypes:
    """Test that the default policy refuses symlink entries."""

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        """Test that the entry is refused by policy, naming what it is."""
        archive = tmp_path / "typed.tar"
        make_tar(archive, link_entry("entry", "target", kind=tarfile.SYMTYPE))

        with pytest.raises(
            ExtractionPolicyError, match="contains symlink, but policy denies it"
        ):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [])


class TestExtractionRefusesUnsafePaths:
    """Test that extraction refuses entry names that escape the output directory.

    The Windows-absolute forms are rejected on POSIX too, so every case here
    runs on all platforms rather than only on the Windows CI leg.
    """

    @pytest.mark.parametrize(
        "entry_name",
        [
            "/etc/passwd",  # POSIX absolute
            "\\evil.txt",  # Windows root-relative
            "C:\\Windows\\evil.txt",  # Windows drive-absolute
            "C:/Windows/evil.txt",  # ... with the other separator
            "C:evil.txt",  # Windows drive-relative
            "\\\\server\\share\\evil.txt",  # UNC
            "\\\\?\\C:\\evil.txt",  # Extended-length prefix
        ],
    )
    def test_rejects_absolute_entry(self, tmp_path: Path, entry_name: str) -> None:
        """Test that an absolute or drive-qualified entry name fails extraction."""
        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry(entry_name))

        with pytest.raises(ExtractionPolicyError, match="absolute path"):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [])

    @pytest.mark.parametrize("entry_name", ["../escape.txt", "sub/../../escape.txt"])
    def test_rejects_parent_traversal(self, tmp_path: Path, entry_name: str) -> None:
        """Test that an entry climbing out of the output directory is refused."""
        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry(entry_name))

        with pytest.raises(ExtractionPolicyError, match="traversal"):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [])

        assert not (tmp_path / "escape.txt").exists()

    def test_backslash_traversal_stays_contained(self, tmp_path: Path) -> None:
        """Test that a backslash-separated traversal cannot escape on any platform.

        On Windows `..\\..\\escape.txt` is a real traversal and is refused; on
        POSIX it is one oddly-named file.
        """
        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry("..\\..\\escape.txt"))
        out = tmp_path / "out"

        if sys.platform == "win32":
            with pytest.raises(ExtractionPolicyError, match="traversal"):
                _core.extract_archive(str(archive), str(out), [])

        else:
            _core.extract_archive(str(archive), str(out), [])
            assert (out / "..\\..\\escape.txt").read_bytes() == b"x"

        assert not (tmp_path / "escape.txt").exists()
        assert not (tmp_path.parent / "escape.txt").exists()

    @pytest.mark.parametrize(
        "entry_name", ["notes.txt:evil.exe", "notes.txt:evil.exe:$DATA"]
    )
    def test_alternate_data_stream_writes_no_stream(
        self, tmp_path: Path, entry_name: str
    ) -> None:
        """Test that an NTFS stream entry never attaches content to a real file.

        On Windows the colon is a stream separator and the entry is refused; on
        POSIX it is an ordinary filename character, so the entry extracts as one
        literally-named file.
        """
        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry(entry_name))
        out = tmp_path / "out"

        if sys.platform == "win32":
            with pytest.raises(ExtractionPolicyError, match="alternate data stream"):
                _core.extract_archive(str(archive), str(out), [])

        else:
            _core.extract_archive(str(archive), str(out), [])
            assert (out / entry_name).read_bytes() == b"x"

        assert not (out / "notes.txt").exists()

    @pytest.mark.usefixtures("needs_symlinks")
    def test_rejects_entry_writing_through_a_symlinked_component(
        self, tmp_path: Path
    ) -> None:
        """Test that an entry cannot write through a symlink already in the output."""
        outside = tmp_path / "outside"
        outside.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (out / "link").symlink_to(outside, target_is_directory=True)

        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry("link/sub/escape.txt"))

        with pytest.raises(ExtractionPolicyError, match="traversal"):
            _core.extract_archive(str(archive), str(out), [])

        assert not (outside / "sub").exists()

    @pytest.mark.skipif(
        sys.platform != "win32", reason="junctions are a Windows filesystem feature"
    )
    def test_rejects_entry_writing_through_a_junction(self, tmp_path: Path) -> None:
        """Test the junction form of the symlinked-component escape.

        The Windows equivalent of the test above; junctions are used rather than
        `mklink /D` symlinks because creating one needs no admin rights.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(out / "link"), str(outside)],
            check=True,
            capture_output=True,
        )

        archive = tmp_path / "evil.tar"
        make_tar(archive, file_entry("link/sub/escape.txt"))

        with pytest.raises(ExtractionPolicyError, match="traversal"):
            _core.extract_archive(str(archive), str(out), [])

        assert not (outside / "sub").exists()

    def test_accepts_nested_entry(self, tmp_path: Path) -> None:
        """Test that a legitimate nested entry is still extracted.

        Guards the containment check against over-rejecting: it compares the
        byte after the resolved root prefix, which is a backslash on Windows.
        """
        archive = tmp_path / "nested.tar"
        make_tar(archive, file_entry("sub/deep/leaf.txt"))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "sub" / "deep" / "leaf.txt").read_bytes() == b"x"


class TestExtractionSizeCap:
    """Test that `max_total_size` bounds what an archive can write."""

    def test_no_cap_by_default(self, tmp_path: Path) -> None:
        """Test that the shipped default places no limit on extracted bytes."""
        archive = tmp_path / "big.tar"
        make_tar(archive, file_entry("big.bin", 200_000))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "big.bin").stat().st_size == 200_000

    def test_refuses_archive_over_the_cap(self, tmp_path: Path) -> None:
        """Test that a total past the cap is refused with nothing written."""
        archive = tmp_path / "bomb.tar"
        make_tar(archive, file_entry("big.bin", 200_000))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="maximum extracted size"):
            _core.extract_archive(str(archive), str(out), [], max_total_size=1000)

        assert list(out.iterdir()) == []

    def test_cap_applies_to_the_total_not_each_entry(self, tmp_path: Path) -> None:
        """Test that entries individually under the cap still fail in total."""
        archive = tmp_path / "many.tar"
        make_tar(archive, *(file_entry(f"f{i}.bin", 400) for i in range(10)))

        with pytest.raises(ExtractionPolicyError, match="maximum extracted size"):
            _core.extract_archive(
                str(archive), str(tmp_path / "out"), [], max_total_size=1000
            )

    def test_entry_under_the_cap_extracts(self, tmp_path: Path) -> None:
        """Test that the cap does not over-reject an archive that fits."""
        archive = tmp_path / "small.tar"
        make_tar(archive, file_entry("small.bin", 500))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], max_total_size=1000)

        assert (out / "small.bin").stat().st_size == 500

    def test_understated_entry_size_is_still_capped(self, tmp_path: Path) -> None:
        """Test that the cap counts bytes written, not the declared size.

        A zip's central directory is the only thing the pre-extraction pass can
        read sizes from, and it is attacker-controlled: patched to claim ten
        bytes, this entry passes that pass and has to be stopped mid-write.
        """
        archive = tmp_path / "lie.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", b"x" * 200_000)

        data = bytearray(archive.read_bytes())
        central_dir = data.find(b"PK\x01\x02")
        struct.pack_into("<I", data, central_dir + 24, 10)  # Uncompressed size
        archive.write_bytes(bytes(data))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="maximum extracted size"):
            _core.extract_archive(str(archive), str(out), [], max_total_size=1000)

        assert not (out / "big.bin").exists()


class TestExtractionDepthLimit:
    """Test that `max_depth` bounds how deeply an entry may nest."""

    def test_refuses_entry_past_the_depth_limit(self, tmp_path: Path) -> None:
        """Test that an entry nested past the default limit is refused."""
        archive = tmp_path / "deep.tar"
        make_tar(archive, file_entry("/".join(f"d{i}" for i in range(40)) + "/x"))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="max depth"):
            _core.extract_archive(str(archive), str(out), [])

        assert list(out.iterdir()) == []

    def test_accepts_entry_within_the_depth_limit(self, tmp_path: Path) -> None:
        """Test that an entry just inside the limit still extracts."""
        archive = tmp_path / "deep.tar"
        parts = [f"d{i}" for i in range(31)]
        make_tar(archive, file_entry("/".join([*parts, "leaf.txt"])))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert out.joinpath(*parts, "leaf.txt").exists()

    def test_depth_limit_is_configurable(self, tmp_path: Path) -> None:
        """Test that a tighter limit refuses what the default accepts."""
        archive = tmp_path / "nested.tar"
        make_tar(archive, file_entry("a/b/c/leaf.txt"))

        with pytest.raises(ExtractionPolicyError, match="max depth"):
            _core.extract_archive(str(archive), str(tmp_path / "out"), [], max_depth=2)


class TestExtractionMetadata:
    """Test that modes and modification times are restored as the policy says."""

    def test_restores_mtime_by_default(self, tmp_path: Path) -> None:
        """Test that the entry's modification time survives extraction."""
        archive = tmp_path / "meta.tar"
        make_tar(archive, file_entry("a.txt", mtime=1_000_000_000))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "a.txt").stat().st_mtime == pytest.approx(1_000_000_000, abs=2)

    def test_read_only_entry_keeps_its_mode_and_mtime(self, tmp_path: Path) -> None:
        """Test that both are set on the open file, read-only included.

        On Windows the mode is only the read-only attribute.
        """
        archive = tmp_path / "meta.tar"
        make_tar(archive, file_entry("a.txt", mode=0o444, mtime=1_000_000_000))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        extracted = (out / "a.txt").stat()
        assert extracted.st_mode & 0o222 == 0
        assert extracted.st_mtime == pytest.approx(1_000_000_000, abs=2)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_restores_mode_by_default(self, tmp_path: Path) -> None:
        """Test that the entry's permission bits survive extraction."""
        archive = tmp_path / "meta.tar"
        make_tar(archive, file_entry("a.txt", mode=0o640))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "a.txt").stat().st_mode & 0o777 == 0o640

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    @pytest.mark.parametrize(
        ("mode", "exact", "expected"),
        [
            (0o4755, False, 0o755),
            (0o777, False, 0o777),
            (0o777, True, 0o777),
        ],
        ids=["setuid", "umask", "exact"],
    )
    def test_mode_is_masked_unless_exact(
        self, tmp_path: Path, mode: int, exact: bool, expected: int
    ) -> None:
        """Test that setuid and the umask's bits go unless exact modes are asked for."""
        archive = tmp_path / "meta.tar"
        make_tar(archive, file_entry("a.txt", mode=mode))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [], exact_permissions=exact)

        if not exact:
            expected &= ~umask()
        assert (out / "a.txt").stat().st_mode & 0o7777 == expected

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_directory_mode_is_masked_too(self, tmp_path: Path) -> None:
        """Test that a directory loses setgid and the umask's bits by default."""
        archive = tmp_path / "meta.tar"
        make_tar(archive, dir_entry("d", 0o2777))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "d").stat().st_mode & 0o7777 == 0o777 & ~umask()


class TestExtractionIsAllOrNothing:
    """Test that a refused entry stops anything from being written."""

    def test_later_bad_entry_blocks_the_earlier_good_one(self, tmp_path: Path) -> None:
        """Test that an escape in the second entry keeps the first off disk."""
        archive = tmp_path / "mixed.tar"
        make_tar(archive, file_entry("good.txt"), file_entry("../escape.txt"))
        out = tmp_path / "out"

        with pytest.raises(ExtractionPolicyError, match="traversal"):
            _core.extract_archive(str(archive), str(out), [])

        assert not (out / "good.txt").exists()
        assert not (tmp_path / "escape.txt").exists()

    def test_parent_segment_check_allows_a_leading_dot_name(
        self, tmp_path: Path
    ) -> None:
        """Test that `..data` is a filename, not a traversal."""
        archive = tmp_path / "dots.tar"
        make_tar(archive, file_entry("..data"))
        out = tmp_path / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "..data").read_bytes() == b"x"

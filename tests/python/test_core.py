"""Tests for the core compression/decompression functionality."""

import io
import logging
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import orjson
import pytest

from compresso import (
    BackendError,
    Cancelled,
    CorruptDataError,
    Error,
    ExtractionPolicyError,
    HeaderError,
    _core,
    compress_file,
    decompress_file,
)
from compresso._core import get_capabilities


def _value_error(call: Callable[[], object]) -> str | None:
    """Run `call`, returning its ValueError's message, or None if it succeeds.

    Args:
        call: The function to run.

    Returns:
        The ValueError's message, or None if the call succeeds.
    """
    try:
        call()

    except ValueError as e:
        return str(e)

    return None


class TestCoreExceptions:
    """Test custom exception classes."""

    @pytest.mark.parametrize(
        "exc",
        [HeaderError, BackendError, CorruptDataError, ExtractionPolicyError, Cancelled],
    )
    def test_every_error_derives_from_error(self, exc: type[Exception]) -> None:
        """Test that catching Error catches every exception the core raises."""
        assert issubclass(Error, Exception)
        assert issubclass(exc, Error)

    def test_corrupt_data_is_not_a_backend_error(self) -> None:
        """Test that bad input is told apart from a backend failure."""
        assert not issubclass(CorruptDataError, BackendError)


class TestCapabilities:
    """Test the get_capabilities function."""

    def test_capabilities_report_each_level_range(self):
        """Test that each backend reports its own level range."""
        ranges = {
            cap["name"]: (cap["min_level"], cap["max_level"])
            for cap in get_capabilities()
            if cap is not None
        }
        assert ranges == {
            "zlib": (0, 9),
            "bzip2": (1, 9),
            "lzma": (0, 9),
            "zstd": (1, 22),
            "lz4": (0, 12),
            "snappy": (None, None),
        }


class TestArchiveCapabilities:
    """Test the archive_capabilities function."""

    def test_archive_capabilities_structure(self) -> None:
        """Test that each archive backend has the expected structure."""
        caps = _core.archive_capabilities()
        assert {cap["name"] for cap in caps} >= {"tar", "zip"}
        for cap in caps:
            assert set(cap) == {"name", "streaming", "compression"}
            assert isinstance(cap["streaming"], bool)
            assert isinstance(cap["compression"], bool)

    @pytest.mark.skipif(
        sys.version_info >= (3, 12), reason="True/False are immortal from 3.12"
    )
    def test_archive_capabilities_does_not_leak_bools(self) -> None:
        """Test that repeated calls leave the refcounts of True/False unchanged."""
        _core.archive_capabilities()
        before = sys.getrefcount(True), sys.getrefcount(False)
        for _ in range(100):
            _core.archive_capabilities()
        assert (sys.getrefcount(True), sys.getrefcount(False)) == before


class TestCompressFile:
    """Test the compress_file function."""

    def test_round_trip_every_backend(
        self, sample_binary_file: Path, temp_dir: Path, compression_algo: str
    ) -> None:
        """Test that each backend's output decompresses back to the original bytes."""
        compressed = temp_dir / f"{compression_algo}.comp"
        restored = temp_dir / f"{compression_algo}.out"

        written = compress_file(
            str(sample_binary_file), str(compressed), compression_algo, "balanced", -1
        )
        assert written == str(compressed)

        assert decompress_file(str(compressed), str(restored), "") == 0
        assert restored.read_bytes() == sample_binary_file.read_bytes()

    def test_unknown_strategy_is_refused(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a misspelt strategy raises rather than meaning balanced."""
        compressed = temp_dir / "typo.comp"

        with pytest.raises(ValueError, match="Unknown strategy: fsat"):
            compress_file(str(sample_text_file), str(compressed), "", "fsat", -1)

        assert not compressed.exists()

    @pytest.mark.parametrize("strategy", ["fast", "balanced", "max_ratio"])
    def test_round_trip_every_strategy(
        self, sample_text_file: Path, temp_dir: Path, strategy: str
    ) -> None:
        """Test that the backend each strategy picks round-trips too.

        No algorithm is named, so the strategy is what chooses the backend.
        """
        compressed = temp_dir / f"{strategy}.comp"
        restored = temp_dir / f"{strategy}.out"

        compress_file(str(sample_text_file), str(compressed), "", strategy, -1)
        decompress_file(str(compressed), str(restored), "")

        assert restored.read_bytes() == sample_text_file.read_bytes()

    def test_compress_nonexistent_file(self, temp_dir: Path) -> None:
        """Test compressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.txt"
        output_file = temp_dir / "output.comp"

        with pytest.raises(FileNotFoundError):
            compress_file(str(input_file), str(output_file), "zlib", "balanced", 6)

    def test_compress_empty_file(self, empty_file: Path, temp_dir: Path) -> None:
        """Test compressing an empty file."""
        output_file = temp_dir / "compressed_empty.comp"

        with pytest.raises(ValueError):
            compress_file(str(empty_file), str(output_file), "zlib", "balanced", 6)

    def test_compress_large_file(
        self, large_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test compressing a large file."""
        output_file = temp_dir / "compressed_large.comp"

        result = compress_file(
            str(large_compressible_file), str(output_file), "zlib", "balanced", 6
        )

        assert result == str(output_file)
        # Highly compressible content should be much smaller
        assert output_file.stat().st_size < large_compressible_file.stat().st_size / 10


class TestDecompressFile:
    """Test the decompress_file function."""

    def test_decompress_nonexistent_file(self, temp_dir: Path) -> None:
        """Test decompressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.comp"
        output_file = temp_dir / "output.txt"

        with pytest.raises(FileNotFoundError):
            decompress_file(str(input_file), str(output_file), "")

    def test_decompress_directory(self, temp_dir: Path) -> None:
        """Test that a directory is refused as unreadable, not as unrecognised."""
        # Windows refuses to open a directory at all, so it reports EACCES
        with pytest.raises((IsADirectoryError, PermissionError)):
            decompress_file(str(temp_dir), str(temp_dir / "output.txt"), "")

    def test_decompress_invalid_file(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a file in no recognised format is refused as such."""
        output_file = temp_dir / "output.txt"

        with pytest.raises(Error, match="Unknown or unsupported format"):
            decompress_file(str(sample_text_file), str(output_file), "")

    def test_round_trip_small_file(self, small_file: Path, temp_dir: Path) -> None:
        """Test compression and decompression of very small files."""
        compressed_file = temp_dir / "compressed_small.comp"
        decompressed_file = temp_dir / "decompressed_small.txt"

        original_content = small_file.read_text()

        compress_file(str(small_file), str(compressed_file), "zlib", "balanced", 6)

        decompress_file(str(compressed_file), str(decompressed_file), "")

        assert decompressed_file.read_text() == original_content

    @pytest.mark.parametrize(
        "name",
        [
            "café-naïve",  # Latin-1 range, but not in every code page
            "日本語のファイル",  # Outside any single-byte code page
            "Ω-πυκνότητα",
            "файл-данных",
            "emoji-🗜️-file",  # Outside the BMP: a surrogate pair in UTF-16
        ],
    )
    def test_round_trip_non_ascii_filename(self, temp_dir: Path, name: str) -> None:
        """Test that paths outside ASCII survive a compress/decompress round trip.

        Paths reach the C layer as UTF-8: on Windows the narrow CRT would read
        those bytes in the active code page and open the wrong file, so this is the
        check that the wide-character path handling works.
        """
        source = temp_dir / f"{name}.txt"
        content = f"contents of {name}" * 50
        source.write_text(content, encoding="utf-8")

        compressed_file = temp_dir / f"{name}.comp"
        decompressed_file = temp_dir / f"{name}-restored.txt"

        compress_file(str(source), str(compressed_file), "zlib", "balanced", 6)
        assert compressed_file.is_file()

        decompress_file(str(compressed_file), str(decompressed_file), "")

        assert decompressed_file.read_text(encoding="utf-8") == content


class TestStandaloneFormatErrors:
    """Test refusing a format the single-file entry points cannot use.

    An archive format resolves to a real `Format` but has no standalone
    handler, so it reaches the "not supported" branch.
    """

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_archive_format_is_refused_not_fatal(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that an archive format raises rather than reading a bad pointer."""
        with pytest.raises(ValueError, match="cannot compress a single file"):
            _core.compress_standalone(
                str(sample_text_file), str(temp_dir / f"out.{fmt}"), fmt, 3
            )

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_archive_format_is_refused_when_decompressing(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that the decompression side has the same defect."""
        with pytest.raises(ValueError, match="cannot decompress a single file"):
            _core.decompress_standalone(
                str(sample_text_file), str(temp_dir / "out.bin"), fmt
            )

    @pytest.mark.parametrize("fmt", [None, "gzip"])
    def test_decompress_nonexistent_file(self, temp_dir: Path, fmt: str | None) -> None:
        """Test that a missing input is reported as such, with or without a format."""
        args = [str(temp_dir / "missing.gz"), str(temp_dir / "out.bin")]
        if fmt:
            args.append(fmt)

        with pytest.raises(FileNotFoundError):
            _core.decompress_standalone(*args)

    @pytest.mark.parametrize("fmt", ["bogus", "comp", ""])
    def test_unknown_format_is_refused(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that a name that resolves to no format at all is refused earlier."""
        with pytest.raises(ValueError):
            _core.compress_standalone(
                str(sample_text_file), str(temp_dir / "out.bin"), fmt, 3
            )


class TestListArchiveContentsArgs:
    """Test the argument handling of list_archive_contents."""

    def test_keyword_arguments_are_refused(self, temp_dir: Path) -> None:
        """Test that a stray keyword raises rather than being silently dropped."""
        with pytest.raises(TypeError, match="keyword"):
            _core.list_archive_contents(str(temp_dir / "a.tar"), bogus=1)  # type: ignore[call-arg]  # ty:ignore[unknown-argument]


class TestExtractArchiveFilesArg:
    """Test the validation of extract_archive's `files` selection."""

    @pytest.fixture
    def two_files(self, temp_dir: Path) -> Path:
        """A tar holding a.txt and b.txt.

        Args:
            temp_dir: Pytest temporary path fixture.

        Returns:
            Path to the tar.
        """
        archive = temp_dir / "two.tar"
        _tar_of(archive, [("a.txt", 0o644, 0), ("b.txt", 0o644, 0)])

        return archive

    @pytest.mark.parametrize(
        "files",
        [["a.txt"], ("a.txt",), iter(["a.txt"])],
        ids=["list", "tuple", "iterator"],
    )
    def test_any_sequence_selects(
        self, two_files: Path, temp_dir: Path, files: object
    ) -> None:
        """Test that a tuple or iterator selects like a list, not as "everything"."""
        out = temp_dir / "out"

        _core.extract_archive(str(two_files), str(out), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

        assert sorted(p.name for p in out.iterdir()) == ["a.txt"]

    def test_empty_selects_everything(self, two_files: Path, temp_dir: Path) -> None:
        """Test that no names at all extracts every entry."""
        out = temp_dir / "out"

        _core.extract_archive(str(two_files), str(out), ())

        assert sorted(p.name for p in out.iterdir()) == ["a.txt", "b.txt"]

    @pytest.mark.parametrize("files", [["a.txt", 1], [None]], ids=["int", "none"])
    def test_non_str_name_is_refused(
        self, two_files: Path, temp_dir: Path, files: list[object]
    ) -> None:
        """Test that a name that isn't a str raises rather than crashing."""
        with pytest.raises(TypeError, match="files must contain only str"):
            _core.extract_archive(str(two_files), str(temp_dir / "out"), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    @pytest.mark.parametrize("files", ["a.txt", b"a.txt"], ids=["str", "bytes"])
    def test_bare_string_is_refused(
        self, two_files: Path, temp_dir: Path, files: object
    ) -> None:
        """Test that one name passed bare isn't read as a name per character."""
        with pytest.raises(TypeError, match="files must be a sequence of str"):
            _core.extract_archive(str(two_files), str(temp_dir / "out"), files)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


class TestRecognisedButUnsupportedArchive:
    """Test that a detected archive format without a backend names itself."""

    @pytest.fixture
    def fake_7z(self, temp_dir: Path) -> Path:
        """A file that detects as 7z by its magic bytes alone.

        Args:
            temp_dir: Pytest temporary path fixture.

        Returns:
            Path to the fake 7z file.
        """
        path = temp_dir / "fake.7z"
        path.write_bytes(b"7z\xbc\xaf\x27\x1c" + bytes(32))

        return path

    def test_listing_names_the_format(self, fake_7z: Path) -> None:
        """Test that listing reports 7z rather than a generic failure."""
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.list_archive_contents(str(fake_7z))

    def test_extracting_names_the_format(self, fake_7z: Path, temp_dir: Path) -> None:
        """Test that extraction reports 7z and writes nothing."""
        out = temp_dir / "out"
        out.mkdir()
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.extract_archive(str(fake_7z), str(out), [])
        assert list(out.iterdir()) == []

    def test_creating_names_the_format(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that creation reports 7z and leaves no output behind."""
        dest = temp_dir / "new.7z"
        with pytest.raises(BackendError, match="7z archives are recognised"):
            _core.create_archive(str(dest), "7z", [str(sample_text_file)])
        assert not dest.exists()


def _two_entry_tar(path: Path) -> bytes:
    """Write a GNU tar of two 5000-byte entries, returning its bytes.

    Args:
        path: Where to write the tar.

    Returns:
        The tar's bytes: a 512-byte header and 5120 bytes of data per entry.
    """
    data = os.urandom(5000)
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as tf:
        for name in ("a.bin", "b.bin"):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    return path.read_bytes()


class TestArchiveErrorTypes:
    """Test that archive failures say whether the input, the OS or policy failed."""

    def test_damaged_later_tar_header_is_corrupt_data(self, temp_dir: Path) -> None:
        """Test that a bad checksum on the second header is corrupt input."""
        damaged = bytearray(_two_entry_tar(temp_dir / "good.tar"))
        damaged[512 + 5120 + 100] ^= 0xFF
        archive = temp_dir / "damaged.tar"
        archive.write_bytes(damaged)

        with pytest.raises(CorruptDataError, match="Damaged tar archive"):
            _core.extract_archive(str(archive), str(temp_dir / "out"), [])

    def test_truncated_tar_is_corrupt_data(self, temp_dir: Path) -> None:
        """Test that a tar cut off inside an entry's data is corrupt input."""
        archive = temp_dir / "truncated.tar"
        archive.write_bytes(_two_entry_tar(temp_dir / "good.tar")[:3000])

        with pytest.raises(CorruptDataError, match="Truncated"):
            _core.list_archive_contents(str(archive))

    def test_damaged_zip_data_is_corrupt_data(self, temp_dir: Path) -> None:
        """Test that a zip entry whose deflate stream is damaged is corrupt input."""
        good = temp_dir / "good.zip"
        with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", b"hello world " * 500)
        damaged = bytearray(good.read_bytes())
        damaged[60] ^= 0xFF  # Inside the first entry's compressed data
        archive = temp_dir / "damaged.zip"
        archive.write_bytes(damaged)

        with pytest.raises(CorruptDataError):
            _core.extract_archive(str(archive), str(temp_dir / "out"), [])

    def test_truncated_zip_is_corrupt_data(self, temp_dir: Path) -> None:
        """Test that a zip missing the end of its central directory is corrupt."""
        good = temp_dir / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("a.txt", b"hello")
        archive = temp_dir / "truncated.zip"
        archive.write_bytes(good.read_bytes()[:-30])

        with pytest.raises(CorruptDataError):
            _core.list_archive_contents(str(archive))

    @pytest.mark.parametrize("call", ["extract", "list"])
    def test_missing_archive_is_file_not_found(self, temp_dir: Path, call: str) -> None:
        """Test that a missing archive is an OS error, not an unknown format."""
        missing = str(temp_dir / "missing.tar")

        with pytest.raises(FileNotFoundError):
            if call == "extract":
                _core.extract_archive(missing, str(temp_dir / "out"), [])
            else:
                _core.list_archive_contents(missing)

    # libzip refuses a name that isn't UTF-8, which only Linux allows
    @pytest.mark.skipif(sys.platform != "linux", reason="needs non-UTF-8 names")
    @pytest.mark.parametrize("kind", ["directory", "symlink"])
    def test_entry_refused_inside_a_tree_fails_the_archive(
        self, temp_dir: Path, kind: str
    ) -> None:
        """Test that a nested entry the writer refuses fails the whole job."""
        tree = temp_dir / "tree"
        tree.mkdir()
        bad_name = os.fsencode(tree) + b"/bad\xff"
        if kind == "directory":
            os.mkdir(bad_name)
        else:
            os.symlink(b"target", bad_name)
        output = temp_dir / "out.zip"

        with pytest.raises(BackendError, match=f"Failed to add {kind}"):
            _core.create_archive(str(output), "zip", [str(tree)])

        assert not output.exists()

    def test_missing_output_dir_parents_are_created(self, temp_dir: Path) -> None:
        """Test that every missing parent is made, whichever separator the path uses.

        Also includes str() of a Windows path, which uses backslashes.
        """
        archive = temp_dir / "x.tar"
        _tar_of(archive, [("f", 0o644, 0)])
        out = temp_dir / "a" / "b" / "c"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "f").read_bytes() == b"x"

    def test_output_dir_that_is_a_file_is_not_a_directory(self, temp_dir: Path) -> None:
        """Test that the output path itself is named, before any entry is tried."""
        archive = temp_dir / "x.tar"
        _tar_of(archive, [("f", 0o644, 0)])
        not_a_dir = temp_dir / "afile"
        not_a_dir.write_text("x")

        with pytest.raises(NotADirectoryError) as info:
            _core.extract_archive(str(archive), str(not_a_dir), [])

        assert info.value.filename == str(not_a_dir)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
    def test_output_dir_that_cannot_be_created_says_why(self, temp_dir: Path) -> None:
        """Test that a read-only parent is a permission error, not a missing path."""
        archive = temp_dir / "x.tar"
        _tar_of(archive, [("f", 0o644, 0)])
        read_only = temp_dir / "ro"
        read_only.mkdir(mode=0o555)

        with pytest.raises(PermissionError) as info:
            _core.extract_archive(str(archive), str(read_only / "out"), [])

        assert info.value.filename == str(read_only / "out")

    @pytest.mark.parametrize("fmt", ["tar", "zip", "tar.gz"])
    def test_failed_walk_reports_its_own_error(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that abandoning the half-built archive doesn't replace the error."""
        missing = temp_dir / "missing"
        output = temp_dir / f"out.{fmt}"

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(
                str(output), fmt, [str(sample_text_file), str(missing)]
            )

        assert info.value.filename == str(missing)
        assert sorted(p.name for p in temp_dir.iterdir()) == [sample_text_file.name]

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_unwritable_destination_is_an_os_error(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that a destination in a missing directory carries its errno."""
        dest = temp_dir / "missing" / f"out.{fmt}"

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(str(dest), fmt, [str(sample_text_file)])

        assert info.value.filename == str(dest)


# Runs in a child process: a FIFO opened for reading blocks with the GIL held,
# so a regression would hang the test worker rather than fail
_ARCHIVE_AND_LIST = """
import logging, sys
import orjson
logging.basicConfig(format="%(levelname)s %(message)s")
from compresso import _core
output, fmt, *inputs = sys.argv[1:]
_core.create_archive(output, fmt, inputs)
sys.stdout.buffer.write(orjson.dumps([e["path"] for e in _core.list_archive_contents(output)]))
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
class TestArchiveSkipsSpecialFiles:
    """Test that FIFOs are skipped with a warning instead of being read."""

    @pytest.mark.parametrize("fmt", ["tar", "zip", "tar.gz"])
    def test_fifo_is_skipped_not_read(self, temp_dir: Path, fmt: str) -> None:
        """Test that a FIFO in the tree, or named directly, is left out."""
        tree = temp_dir / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a")
        os.mkfifo(tree / "pipe")
        os.mkfifo(temp_dir / "named_pipe")
        output = temp_dir / f"out.{fmt}"

        result = subprocess.run(
            [sys.executable, "-c", _ARCHIVE_AND_LIST, str(output), fmt]
            + [str(tree), str(temp_dir / "named_pipe")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == ["tree/", "tree/a.txt"]
        for skipped in (tree / "pipe", temp_dir / "named_pipe"):
            assert f"WARNING Skipped {skipped}:" in result.stderr


class TestArchiveSkipsItsOwnOutput:
    """Test that archiving a tree into itself leaves the archive out."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    @pytest.mark.parametrize("overwrite", [False, True], ids=["new", "overwrite"])
    def test_output_in_the_tree_is_not_archived(
        self,
        temp_dir: Path,
        caplog: pytest.LogCaptureFixture,
        fmt: str,
        overwrite: bool,
    ) -> None:
        """Test that neither the output nor the temp file beside it is archived."""
        tree = temp_dir / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a" * 10000)
        output = tree / f"out.{fmt}"
        if overwrite:
            _core.create_archive(str(output), fmt, [str(tree)])
            caplog.clear()

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.create_archive(str(output), fmt, [str(tree)], overwrite=2)

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert listed == ["tree/", "tree/a.txt"]
        assert any(
            r.getMessage().endswith("it is the archive being created")
            for r in caplog.records
        )
        assert sorted(p.name for p in tree.iterdir()) == ["a.txt", output.name]


def _tar_of(path: Path, entries: list[tuple[str, int, int]]) -> None:
    """Write a tar of directories and files, each with a given mode and mtime.

    Args:
        path: Where to write the tar.
        entries: (name, mode, mtime) per entry, in archive order; a name
            ending in "/" is a directory, anything else a file holding b"x".
    """
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as tf:
        for name, mode, mtime in entries:
            info = tarfile.TarInfo(name.rstrip("/"))
            info.mode = mode
            info.mtime = mtime
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))


class TestDirectoryMetadata:
    """Test that a directory's mode and mtime are applied after its contents."""

    MTIME = 1577836800  # 2020-01-01T00:00:00Z

    def test_directory_mtime_is_restored(self, temp_dir: Path) -> None:
        """Test that writing a directory's files doesn't leave it with today's mtime."""
        archive = temp_dir / "dated.tar"
        _tar_of(archive, [("d/", 0o755, self.MTIME), ("d/f", 0o644, self.MTIME)])

        _core.extract_archive(str(archive), str(temp_dir / "out"), [])

        assert int((temp_dir / "out" / "d").stat().st_mtime) == self.MTIME

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_nested_read_only_directories_are_filled(self, temp_dir: Path) -> None:
        """Test that read-only directories, nested, all get their files and modes."""
        archive = temp_dir / "readonly.tar"
        _tar_of(
            archive,
            [
                ("a/", 0o555, self.MTIME),
                ("a/b/", 0o555, self.MTIME),
                ("a/b/f", 0o644, self.MTIME),
                ("a/g", 0o644, self.MTIME),
            ],
        )
        out = temp_dir / "out"

        _core.extract_archive(str(archive), str(out), [])

        assert (out / "a" / "b" / "f").read_bytes() == b"x"
        assert (out / "a" / "g").read_bytes() == b"x"
        for directory in (out / "a", out / "a" / "b"):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o555
            assert int(directory.stat().st_mtime) == self.MTIME

    def test_existing_directory_keeps_its_metadata(self, temp_dir: Path) -> None:
        """Test that merging into a directory that already exists leaves it as is."""
        archive = temp_dir / "dated.tar"
        _tar_of(archive, [("d/", 0o700, self.MTIME), ("d/f", 0o644, self.MTIME)])
        existing = temp_dir / "out" / "d"
        existing.mkdir(parents=True)
        before = existing.stat()

        _core.extract_archive(str(archive), str(temp_dir / "out"), [])

        assert existing.stat().st_mode == before.st_mode
        assert int(existing.stat().st_mtime) != self.MTIME

    def test_opt_in_restores_an_existing_directory(self, temp_dir: Path) -> None:
        """Test that overwrite_dir_metadata applies GNU tar's behaviour."""
        archive = temp_dir / "dated.tar"
        _tar_of(archive, [("d/", 0o700, self.MTIME), ("d/f", 0o644, self.MTIME)])
        existing = temp_dir / "out" / "d"
        existing.mkdir(parents=True)

        _core.extract_archive(
            str(archive), str(temp_dir / "out"), [], overwrite_dir_metadata=True
        )

        assert int(existing.stat().st_mtime) == self.MTIME
        if sys.platform != "win32":
            assert stat.S_IMODE(existing.stat().st_mode) == 0o700

    def test_opt_in_covers_a_directory_listed_after_its_contents(
        self, temp_dir: Path
    ) -> None:
        """Test that a directory created as a parent still gets its own entry's metadata."""
        archive = temp_dir / "late.tar"
        _tar_of(archive, [("d/f", 0o644, self.MTIME), ("d/", 0o755, self.MTIME)])
        out = temp_dir / "out"

        _core.extract_archive(str(archive), str(out), [], overwrite_dir_metadata=True)

        assert int((out / "d").stat().st_mtime) == self.MTIME

    def test_opt_in_never_touches_an_existing_directory_when_skipping(
        self, temp_dir: Path
    ) -> None:
        """Test that SKIP mode keeps its promise to leave existing paths alone."""
        archive = temp_dir / "dated.tar"
        _tar_of(archive, [("d/", 0o700, self.MTIME), ("d/f", 0o644, self.MTIME)])
        existing = temp_dir / "out" / "d"
        existing.mkdir(parents=True)
        before = existing.stat()

        _core.extract_archive(
            str(archive),
            str(temp_dir / "out"),
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
        self, temp_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that the entry is listed, its name kept by surrogateescape."""
        archive = temp_dir / "warn.tar"
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
        self, temp_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that extraction carries on, warning once despite two passes."""
        archive = temp_dir / "warn.tar"
        _tar_with_non_utf8_pax_name(archive)
        out = temp_dir / "out"

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.extract_archive(str(archive), str(out), [])

        written = os.listdir(os.fsencode(out / "dir"))
        assert written == [b"\xff" + b"n" * 119 + b".txt"]
        assert len(caplog.records) == 1


def _tar_with_hardlink(path: Path, target: str, *, link: str = "d/b") -> None:
    """Write a tar of file d/a then a hardlink entry `link` naming `target`.

    Args:
        path: Where to write the tar.
        target: The archive path the hardlink names.
        link: The hardlink entry's own name.
    """
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as tf:
        info = tarfile.TarInfo("d/a")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
        hardlink = tarfile.TarInfo(link)
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = target
        tf.addfile(hardlink)


class TestHardlinkExtraction:
    """Test that a hardlink's target is held to the rules of an entry's path."""

    @pytest.mark.parametrize("target", ["../outside", "/etc/passwd"])
    def test_target_outside_the_root_is_refused(
        self, temp_dir: Path, target: str
    ) -> None:
        """Test that the whole archive is refused before anything is written."""
        archive = temp_dir / "evil.tar"
        _tar_with_hardlink(archive, target)
        out = temp_dir / "out"

        with pytest.raises(ExtractionPolicyError):
            _core.extract_archive(str(archive), str(out), [])

        assert not (out / "d").exists()

    def test_unselected_target_is_reported(self, temp_dir: Path) -> None:
        """Test that a link whose target wasn't extracted names the missing file."""
        archive = temp_dir / "links.tar"
        _tar_with_hardlink(archive, "d/a")

        with pytest.raises(FileNotFoundError):
            _core.extract_archive(str(archive), str(temp_dir / "out"), ["d/b"])

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
    def test_target_replaced_by_a_symlink_is_refused(self, temp_dir: Path) -> None:
        """Test that a link is never made to whatever a planted symlink points at."""
        archive = temp_dir / "links.tar"
        _tar_with_hardlink(archive, "d/s")
        out = temp_dir / "out"
        (out / "d").mkdir(parents=True)
        (temp_dir / "secret").write_text("secret")
        (out / "d" / "s").symlink_to(temp_dir / "secret")

        with pytest.raises(ExtractionPolicyError, match="not a regular file"):
            _core.extract_archive(str(archive), str(out), [], overwrite=2)

    def test_existing_link_path_follows_the_overwrite_mode(
        self, temp_dir: Path
    ) -> None:
        """Test ERROR, SKIP, OVERWRITE and RENAME against an existing d/b."""
        archive = temp_dir / "links.tar"
        _tar_with_hardlink(archive, "d/a")

        def extract(mode: int) -> Path:
            out = temp_dir / f"out{mode}"
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


def _compress_to(fmt: str, src: Path, dst: Path, overwrite: int = 0) -> None:
    """Compress `src` to `dst` as a .comp or a standalone gzip file."""
    if fmt == "comp":
        compress_file(str(src), str(dst), "zlib", "", -1, overwrite=overwrite)
    else:
        _core.compress_standalone(str(src), str(dst), "gzip", overwrite=overwrite)


class TestOutputsCommitOnlyOnSuccess:
    """Test that outputs go through a temp file, so a failure leaves no trace."""

    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    def test_failed_decompression_keeps_the_existing_file(
        self, temp_dir: Path, fmt: str
    ) -> None:
        """Test that corrupt input onto an existing file neither truncates nor removes it."""
        source = temp_dir / "source.bin"
        source.write_bytes(os.urandom(64 * 1024))
        compressed = temp_dir / f"source.{fmt}"
        _compress_to(fmt, source, compressed)
        data = compressed.read_bytes()
        compressed.write_bytes(data[: len(data) // 2])

        existing = temp_dir / "existing.bin"
        existing.write_bytes(b"keep me")

        with pytest.raises(CorruptDataError):
            decompress_file(str(compressed), str(existing), "")

        assert existing.read_bytes() == b"keep me"
        assert not list(temp_dir.glob(".compresso-*"))

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    def test_failed_archive_creation_keeps_the_existing_file(
        self, sample_text_file: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that an archive that fails part way never replaces the one there."""
        existing = temp_dir / f"existing.{fmt}"
        existing.write_bytes(b"keep me")

        with pytest.raises(FileNotFoundError):
            _core.create_archive(
                str(existing),
                fmt,
                [str(sample_text_file), str(temp_dir / "missing")],
                overwrite=2,
            )

        assert existing.read_bytes() == b"keep me"
        assert not list(temp_dir.glob(".compresso-*"))

    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    def test_overwriting_the_input_itself_is_refused(
        self, sample_text_file: Path, fmt: str
    ) -> None:
        """Test that an output that is the input file is refused before any work."""
        original = sample_text_file.read_bytes()

        with pytest.raises(ValueError, match="same file"):
            _compress_to(fmt, sample_text_file, sample_text_file, overwrite=2)

        assert sample_text_file.read_bytes() == original


class TestStrategyNames:
    """Test the strategy names the other entry points accept."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda: _core.get_default_backend_for_strategy("fsat"),
            lambda: _core.check_level(5, strategy="fsat"),
            # An explicit algorithm overrides the strategy, but a typo is still one
            lambda: _core.check_level(5, algo="zstd", strategy="fsat"),
        ],
        ids=["default_backend", "check_level", "check_level_with_algo"],
    )
    def test_unknown_name_is_refused(self, call: Callable[[], object]) -> None:
        """Test that a misspelt strategy raises ValueError naming the choices."""
        with pytest.raises(ValueError, match="expected fast, balanced or max_ratio"):
            call()

    @pytest.mark.parametrize("name", ["", "balanced"])
    def test_empty_name_means_balanced(self, name: str) -> None:
        """Test that no strategy picks the same backend as balanced."""
        assert _core.get_default_backend_for_strategy(name) == "zstd"


class TestLevelValidation:
    """Test that each backend, format and container checks its own level range."""

    @pytest.mark.parametrize(
        "algo,strategy,level,match",
        [
            ("zlib", "", 10, r"zlib compression level 10 out of range \(0-9"),
            ("bzip2", "", 0, r"bzip2 compression level 0 out of range \(1-9"),
            ("zstd", "", 0, r"zstd compression level 0 out of range \(1-22"),
            ("zstd", "", 23, r"zstd compression level 23 out of range"),
            ("lz4", "", 13, r"lz4 compression level 13 out of range \(0-12"),
            ("zlib", "", -2, r"zlib compression level -2 out of range"),
            ("snappy", "", 1, "snappy has no compression levels"),
            # No algo: the backend the strategy picks is the one checked
            ("", "fast", 13, "lz4 compression level 13"),
        ],
    )
    def test_compress_file_refuses_out_of_range(
        self,
        sample_text_file: Path,
        temp_dir: Path,
        algo: str,
        strategy: str,
        level: int,
        match: str,
    ) -> None:
        """Test that an out-of-range level is refused before anything is written."""
        dest = temp_dir / "out.comp"
        with pytest.raises(ValueError, match=match):
            compress_file(str(sample_text_file), str(dest), algo, strategy, level)
        assert not dest.exists()

    @pytest.mark.parametrize(
        "algo,level",
        [("zlib", 9), ("bzip2", 1), ("zstd", 22), ("lz4", 12), ("snappy", -1)],
    )
    def test_compress_file_accepts_range_ends(
        self, sample_text_file: Path, temp_dir: Path, algo: str, level: int
    ) -> None:
        """Test that each end of a backend's range, and the default, still work."""
        compress_file(
            str(sample_text_file), str(temp_dir / "out.comp"), algo, "", level
        )

    @pytest.mark.parametrize(
        "fmt,level,match",
        [
            ("gz", 10, r"gzip compression level 10 out of range \(0-9"),
            ("zst", 23, r"zstd compression level 23 out of range \(1-22"),
        ],
    )
    def test_compress_standalone_refuses_out_of_range(
        self, sample_text_file: Path, temp_dir: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that the standalone formats check the level too."""
        with pytest.raises(ValueError, match=match):
            _core.compress_standalone(
                str(sample_text_file), str(temp_dir / f"out.{fmt}"), fmt, level
            )

    @pytest.mark.parametrize(
        "fmt,level,match",
        [
            ("tar", 1, "tar has no compression levels"),
            ("zip", 10, r"zip compression level 10 out of range \(0-9"),
            # A codec stage's own range applies, not the container's
            ("tar.zst", 23, r"zstd compression level 23 out of range \(1-22"),
            ("tar.gz", 10, r"gzip compression level 10 out of range"),
        ],
    )
    def test_create_archive_refuses_out_of_range(
        self, sample_text_file: Path, temp_dir: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that archives check the level of whichever stage compresses."""
        dest = temp_dir / f"out.{fmt}"
        with pytest.raises(ValueError, match=match):
            _core.create_archive(str(dest), fmt, [str(sample_text_file)], level)
        assert not dest.exists()

    def test_create_archive_accepts_a_codec_stage_level(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that tar.zst takes zstd's levels even though tar has none."""
        _core.create_archive(
            str(temp_dir / "out.tar.zst"), "tar.zst", [str(sample_text_file)], 19
        )

    @pytest.mark.parametrize(
        "algo,level",
        [("zlib", 9), ("zlib", 10), ("bzip2", 0), ("zstd", 22), ("snappy", 1)],
    )
    def test_check_level_agrees_with_compress_file(
        self, sample_text_file: Path, temp_dir: Path, algo: str, level: int
    ) -> None:
        """Test that the up-front check refuses exactly what compression refuses."""

        checked = _value_error(lambda: _core.check_level(level, algo=algo))
        compressed = _value_error(
            lambda: compress_file(
                str(sample_text_file), str(temp_dir / "o.comp"), algo, "", level
            )
        )
        assert checked == compressed

    @pytest.mark.parametrize(
        "fmt,level", [("gz", 10), ("tar", 1), ("tar.zst", 23), ("zip", 9)]
    )
    def test_check_level_agrees_with_create_archive(
        self, sample_text_file: Path, temp_dir: Path, fmt: str, level: int
    ) -> None:
        """Test that the up-front check matches archive and standalone creation."""

        checked = _value_error(lambda: _core.check_level(level, format=fmt))
        dest = str(temp_dir / f"o.{fmt}")
        if fmt == "gz":
            created = _value_error(
                lambda: _core.compress_standalone(
                    str(sample_text_file), dest, fmt, level
                )
            )
        else:
            created = _value_error(
                lambda: _core.create_archive(dest, fmt, [str(sample_text_file)], level)
            )
        assert checked == created

"""Tests for the core compression/decompression functionality."""

import gzip
import importlib.util
import io
import logging
import lzma
import os
import random
import socket
import stat
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
    IncompleteExtractionError,
    _core,
    compress_file,
    decompress_file,
)
from compresso._core import get_capabilities

from .helpers import (
    dir_entry,
    file_entry,
    link_entry,
    make_tar,
    node_entry,
    run_child,
    umask,
)


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
        [
            HeaderError,
            BackendError,
            CorruptDataError,
            ExtractionPolicyError,
            IncompleteExtractionError,
            Cancelled,
        ],
    )
    def test_every_error_derives_from_error(self, exc: type[Exception]) -> None:
        """Test that catching Error catches every exception the core raises."""
        assert issubclass(Error, Exception)
        assert issubclass(exc, Error)

    def test_corrupt_data_is_not_a_backend_error(self) -> None:
        """Test that bad input is told apart from a backend failure."""
        assert not issubclass(CorruptDataError, BackendError)

    def test_each_module_raises_its_own(self, tmp_path: Path) -> None:
        """Test that a second _core module object raises its own classes."""
        spec = importlib.util.find_spec("compresso._core")
        assert spec and spec.loader
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        assert fresh.Cancelled is not Cancelled

        source = tmp_path / "input.txt"
        source.write_bytes(b"data")
        token = fresh.CancelToken()
        token.cancel()
        with pytest.raises(fresh.Cancelled):
            fresh.compress_file(source, tmp_path / "out.comp", cancel=token)


class TestCapabilities:
    """Test the get_capabilities function."""

    def test_capabilities_report_each_level_range(self):
        """Test that each backend reports its own level range."""
        ranges = {
            cap["name"]: (cap["min_level"], cap["max_level"])
            for cap in get_capabilities()
        }
        assert ranges == {
            "zlib": (0, 9),
            "bzip2": (1, 9),
            "lzma": (0, 9),
            "zstd": (1, 22),
            "lz4": (0, 12),
            "snappy": (None, None),
        }

    @pytest.mark.parametrize("strategy", ["balanced", "fast", "max_ratio"])
    def test_lowest_rank_is_the_strategy_default(self, strategy: str) -> None:
        """Test that the rank explains which backend a strategy picks."""
        caps = get_capabilities()
        best = min(caps, key=lambda cap: cap["rank"][strategy])
        assert best["name"] == _core.get_default_backend_for_strategy(strategy)
        assert sorted(cap["rank"][strategy] for cap in caps) == list(range(6))


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
        self, sample_binary_file: Path, tmp_path: Path, compression_algo: str
    ) -> None:
        """Test that each backend's output decompresses back to the original bytes."""
        compressed = tmp_path / f"{compression_algo}.comp"
        restored = tmp_path / f"{compression_algo}.out"

        written = compress_file(
            str(sample_binary_file), str(compressed), compression_algo, "balanced", -1
        )
        assert written == str(compressed)

        assert decompress_file(str(compressed), str(restored), "") == str(restored)
        assert restored.read_bytes() == sample_binary_file.read_bytes()

    def test_unknown_strategy_is_refused(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a misspelt strategy raises rather than meaning balanced."""
        compressed = tmp_path / "typo.comp"

        with pytest.raises(ValueError, match="Unknown strategy: fsat"):
            compress_file(str(sample_text_file), str(compressed), "", "fsat", -1)

        assert not compressed.exists()

    @pytest.mark.parametrize("strategy", ["fast", "balanced", "max_ratio"])
    def test_round_trip_every_strategy(
        self, sample_text_file: Path, tmp_path: Path, strategy: str
    ) -> None:
        """Test that the backend each strategy picks round-trips too.

        No algorithm is named, so the strategy is what chooses the backend.
        """
        compressed = tmp_path / f"{strategy}.comp"
        restored = tmp_path / f"{strategy}.out"

        compress_file(str(sample_text_file), str(compressed), "", strategy, -1)
        decompress_file(str(compressed), str(restored), "")

        assert restored.read_bytes() == sample_text_file.read_bytes()

    def test_compress_nonexistent_file(self, tmp_path: Path) -> None:
        """Test compressing a file that doesn't exist."""
        input_file = tmp_path / "nonexistent.txt"
        output_file = tmp_path / "output.comp"

        with pytest.raises(FileNotFoundError):
            compress_file(str(input_file), str(output_file), "zlib", "balanced", 6)

    def test_compress_large_file(
        self, large_compressible_file: Path, tmp_path: Path
    ) -> None:
        """Test compressing a large file."""
        output_file = tmp_path / "compressed_large.comp"

        result = compress_file(
            str(large_compressible_file), str(output_file), "zlib", "balanced", 6
        )

        assert result == str(output_file)
        # Highly compressible content should be much smaller
        assert output_file.stat().st_size < large_compressible_file.stat().st_size / 10


class TestDecompressFile:
    """Test the decompress_file function."""

    def test_decompress_nonexistent_file(self, tmp_path: Path) -> None:
        """Test decompressing a file that doesn't exist."""
        input_file = tmp_path / "nonexistent.comp"
        output_file = tmp_path / "output.txt"

        with pytest.raises(FileNotFoundError):
            decompress_file(str(input_file), str(output_file), "")

    def test_decompress_directory(self, tmp_path: Path) -> None:
        """Test that a directory is refused as unreadable, not as unrecognised."""
        # Windows refuses to open a directory at all, so it reports EACCES
        with pytest.raises((IsADirectoryError, PermissionError)):
            decompress_file(str(tmp_path), str(tmp_path / "output.txt"), "")

    def test_decompress_invalid_file(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a file in no recognised format is refused as such."""
        output_file = tmp_path / "output.txt"

        with pytest.raises(Error, match="Unknown or unsupported format"):
            decompress_file(str(sample_text_file), str(output_file), "")

    def test_round_trip_small_file(self, small_file: Path, tmp_path: Path) -> None:
        """Test compression and decompression of very small files."""
        compressed_file = tmp_path / "compressed_small.comp"
        decompressed_file = tmp_path / "decompressed_small.txt"

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
    def test_round_trip_non_ascii_filename(self, tmp_path: Path, name: str) -> None:
        """Test that paths outside ASCII survive a compress/decompress round trip.

        Paths reach the C layer as UTF-8: on Windows the narrow CRT would read
        those bytes in the active code page and open the wrong file, so this is the
        check that the wide-character path handling works.
        """
        source = tmp_path / f"{name}.txt"
        content = f"contents of {name}" * 50
        source.write_text(content, encoding="utf-8")

        compressed_file = tmp_path / f"{name}.comp"
        decompressed_file = tmp_path / f"{name}-restored.txt"

        compress_file(str(source), str(compressed_file), "zlib", "balanced", 6)
        assert compressed_file.is_file()

        decompress_file(str(compressed_file), str(decompressed_file), "")

        assert decompressed_file.read_text(encoding="utf-8") == content

    @pytest.mark.parametrize(("algo", "refused"), [("zstd", True), ("zlib", False)])
    def test_algo_is_checked_against_a_container(
        self, tmp_path: Path, algo: str, refused: bool
    ) -> None:
        """Test that an explicit algo must match a gzip file's engine."""
        source = tmp_path / "data.gz"
        source.write_bytes(gzip.compress(b"payload"))
        output = tmp_path / "data"

        message = _value_error(lambda: decompress_file(source, output, algo))

        if refused:
            assert message == "File was compressed with gzip, not the requested zstd"
            assert not output.exists()
        else:
            assert message is None
            assert output.read_bytes() == b"payload"

    @pytest.mark.parametrize(("algo", "refused"), [("zstd", True), ("zlib", False)])
    def test_algo_is_checked_against_a_comp_header(
        self, small_file: Path, tmp_path: Path, algo: str, refused: bool
    ) -> None:
        """Test that an explicit algo must match the one a .comp header records."""
        compressed = tmp_path / "data.comp"
        compress_file(small_file, compressed, "zlib", "balanced", 6)
        output = tmp_path / "data"

        message = _value_error(lambda: decompress_file(compressed, output, algo))

        if refused:
            assert message == "File was compressed with zlib, not the requested zstd"
            assert not output.exists()
        else:
            assert message is None
            assert output.read_bytes() == small_file.read_bytes()


class TestStandaloneErrors:
    """Test refusing a format the single-file entry points cannot use.

    An archive format resolves to a real `Format` but has no standalone
    handler, so it reaches the "not supported" branch.
    """

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_archive_format_is_refused_not_fatal(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that an archive format raises rather than reading a bad pointer."""
        with pytest.raises(ValueError, match="cannot compress a single file"):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / f"out.{fmt}"), fmt, 3
            )

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_archive_format_is_refused_when_decompressing(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that the decompression side has the same defect."""
        with pytest.raises(ValueError, match="cannot decompress a single file"):
            _core.decompress_standalone(
                str(sample_text_file), str(tmp_path / "out.bin"), fmt
            )

    @pytest.mark.parametrize("fmt", [None, "gzip"])
    def test_decompress_nonexistent_file(self, tmp_path: Path, fmt: str | None) -> None:
        """Test that a missing input is reported as such, with or without a format."""
        args = [str(tmp_path / "missing.gz"), str(tmp_path / "out.bin")]
        if fmt:
            args.append(fmt)

        with pytest.raises(FileNotFoundError):
            _core.decompress_standalone(*args)

    @pytest.mark.parametrize("fmt", ["bogus", "comp", ""])
    def test_unknown_format_is_refused(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a name that resolves to no format at all is refused earlier."""
        with pytest.raises(ValueError):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / "out.bin"), fmt, 3
            )


class TestListArchiveContentsArgs:
    """Test the argument handling of list_archive_contents."""

    def test_keyword_arguments_are_refused(self, tmp_path: Path) -> None:
        """Test that a stray keyword raises rather than being silently dropped."""
        with pytest.raises(TypeError, match="keyword"):
            _core.list_archive_contents(str(tmp_path / "a.tar"), bogus=1)  # type: ignore[call-arg]  # ty:ignore[unknown-argument]


class TestDetectFormat:
    """Test detect_format's handling of a path it cannot read."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Test that a missing file is reported as such, not as "unknown"."""
        with pytest.raises(FileNotFoundError):
            _core.detect_format(tmp_path / "missing.gz")


class TestFormatIsArchive:
    """Test that format_is_archive agrees with what create_archive accepts."""

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [("tar.gz", True), ("tgz", True), ("zip", True), ("zip.gz", False)],
    )
    def test_only_a_valid_pipeline_is_an_archive(
        self, fmt: str, expected: bool
    ) -> None:
        """Test that zip, which compresses its own entries, takes no codec."""
        assert _core.format_is_archive(fmt) is expected


class TestPathArguments:
    """Test how the entry points convert filesystem paths."""

    def test_path_like_round_trip(self, sample_text_file: Path, tmp_path: Path) -> None:
        """Test that Path and bytes paths work, and input_paths takes any sequence."""
        archive = tmp_path / "out.tar"

        _core.create_archive(archive, "tar", (sample_text_file,))
        listed = _core.list_archive_contents(os.fsencode(archive))
        _core.extract_archive(archive, tmp_path / "out", [])

        assert [e["path"] for e in listed] == [sample_text_file.name]
        assert (tmp_path / "out" / sample_text_file.name).read_bytes() == (
            sample_text_file.read_bytes()
        )

    @pytest.mark.parametrize(
        "call",
        [
            lambda src, dst: _core.compress_file(src, dst + "\0x", "zstd", "", -1),
            lambda src, dst: _core.compress_standalone(src, dst + "\0x", "gzip"),
            lambda src, dst: _core.create_archive(dst + "\0x", "tar", [src]),
            lambda src, dst: _core.create_archive(dst, "tar", [src + "\0x"]),
        ],
        ids=["compress_file", "compress_standalone", "archive_output", "archive_input"],
    )
    def test_embedded_nul_is_refused(
        self,
        sample_text_file: Path,
        tmp_path: Path,
        call: Callable[[str, str], object],
    ) -> None:
        """Test that a NUL raises rather than truncating the path."""
        dst = str(tmp_path / "out")

        with pytest.raises(ValueError, match="null"):
            call(str(sample_text_file), dst)

        assert not os.path.exists(dst)

    def test_bare_path_as_input_paths_is_refused(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a path passed bare isn't read as a path per character."""
        with pytest.raises(TypeError, match="sequence of paths"):
            _core.create_archive(
                str(tmp_path / "out.tar"), "tar", str(sample_text_file)
            )


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

    # libzip refuses a name that isn't UTF-8, which only Linux allows
    @pytest.mark.skipif(sys.platform != "linux", reason="needs non-UTF-8 names")
    @pytest.mark.parametrize("kind", ["directory", "symlink"])
    def test_entry_refused_inside_a_tree_fails_the_archive(
        self, tmp_path: Path, kind: str
    ) -> None:
        """Test that a nested entry the writer refuses fails the whole job."""
        tree = tmp_path / "tree"
        tree.mkdir()
        bad_name = os.fsencode(tree) + b"/bad\xff"
        if kind == "directory":
            os.mkdir(bad_name)
        else:
            os.symlink(b"target", bad_name)
        output = tmp_path / "out.zip"

        with pytest.raises(BackendError, match=f"Failed to add {kind}"):
            _core.create_archive(str(output), "zip", [str(tree)])

        assert not output.exists()

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

    @pytest.mark.parametrize("fmt", ["tar", "zip", "tar.gz"])
    def test_failed_walk_reports_its_own_error(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that abandoning the half-built archive doesn't replace the error."""
        missing = tmp_path / "missing"
        output = tmp_path / f"out.{fmt}"

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(
                str(output), fmt, [str(sample_text_file), str(missing)]
            )

        assert info.value.filename == str(missing)
        assert sorted(p.name for p in tmp_path.iterdir()) == [sample_text_file.name]

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_unwritable_destination_is_an_os_error(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a destination in a missing directory carries its errno."""
        dest = tmp_path / "missing" / f"out.{fmt}"

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
class TestArchiveSpecialFiles:
    """Test that tar stores FIFOs and devices, zip skips them, and neither reads them."""

    def test_zip_skips_a_fifo(self, tmp_path: Path) -> None:
        """Test that a FIFO in the tree, or named directly, is left out of a zip."""
        fmt = "zip"
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a")
        os.mkfifo(tree / "pipe")
        os.mkfifo(tmp_path / "named_pipe")
        output = tmp_path / f"out.{fmt}"

        result = run_child(
            _ARCHIVE_AND_LIST, str(output), fmt, str(tree), str(tmp_path / "named_pipe")
        )

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == ["tree/", "tree/a.txt"]
        for skipped in (tree / "pipe", tmp_path / "named_pipe"):
            assert f"WARNING Skipped {skipped}:" in result.stderr

    def test_nothing_left_to_archive_gives_an_empty_archive(
        self, tmp_path: Path
    ) -> None:
        """Test that an archive whose only input is skipped is written empty."""
        os.mkfifo(tmp_path / "pipe")
        output = tmp_path / "out.zip"

        result = run_child(
            _ARCHIVE_AND_LIST, str(output), "zip", str(tmp_path / "pipe")
        )

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == []
        with zipfile.ZipFile(output) as zf:
            assert zf.namelist() == []

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz"])
    def test_tar_stores_a_fifo_not_its_data(self, tmp_path: Path, fmt: str) -> None:
        """Test that a FIFO is stored as a FIFO entry, as GNU tar stores one."""
        tree = tmp_path / "tree"
        tree.mkdir()
        os.mkfifo(tree / "pipe")
        output = tmp_path / f"out.{fmt}"

        result = run_child(_ARCHIVE_AND_LIST, str(output), fmt, str(tree))

        assert result.returncode == 0, result.stderr
        with tarfile.open(output) as tf:
            member = tf.getmember("tree/pipe")
        assert member.isfifo()
        assert member.size == 0
        assert member.mode == stat.S_IMODE((tree / "pipe").stat().st_mode)

    @pytest.mark.skipif(not os.path.exists("/dev/null"), reason="needs /dev/null")
    def test_tar_stores_a_device_with_its_number(self, tmp_path: Path) -> None:
        """Test that a character device keeps its major and minor numbers."""
        output = tmp_path / "out.tar"
        rdev = os.stat("/dev/null").st_rdev

        _core.create_archive(str(output), "tar", ["/dev/null"])

        with tarfile.open(output) as tf:
            member = tf.getmember("null")
        assert member.ischr()
        assert (member.devmajor, member.devminor) == (os.major(rdev), os.minor(rdev))

    def test_tar_skips_a_socket(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that a socket is left out of a tar with a warning, as GNU tar does."""
        # A relative name, since a socket path's length is capped near 100 bytes
        monkeypatch.chdir(tmp_path)
        Path("tree").mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind("tree/sock")
            with caplog.at_level(logging.WARNING):
                _core.create_archive("out.tar", "tar", ["tree"])

        assert [e["path"] for e in _core.list_archive_contents("out.tar")] == ["tree/"]
        assert "Skipped tree/sock: sockets are not archived" in caplog.text


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


class TestArchiveSkipsItsOwnOutput:
    """Test that archiving a tree into itself leaves the archive out."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    @pytest.mark.parametrize("overwrite", [False, True], ids=["new", "overwrite"])
    def test_output_in_the_tree_is_not_archived(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        fmt: str,
        overwrite: bool,
    ) -> None:
        """Test that neither the output nor the temp file beside it is archived."""
        tree = tmp_path / "tree"
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

    def test_progress_total_leaves_out_the_old_archive(self, tmp_path: Path) -> None:
        """Test that the progress total counts only what is archived."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a" * 10000)
        output = tree / "out.tar"
        output.write_bytes(bytes(50000))

        totals: list[int] = []
        _core.create_archive(
            str(output),
            "tar",
            [str(tree)],
            overwrite=2,
            progress=lambda done, total: totals.append(total),
        )

        assert totals
        assert set(totals) == {10000}


# A small thread stack, which recursing per directory level overflowed; run in
# a child process, since that is a crash rather than an exception
_ARCHIVE_ON_SMALL_STACK = """
import sys, threading
from compresso import _core
threading.stack_size(128 * 1024)
t = threading.Thread(target=_core.create_archive, args=(sys.argv[1], "tar", [sys.argv[2]]))
t.start()
t.join()
print(len(_core.list_archive_contents(sys.argv[1])))
"""


class TestArchiveWalk:
    """Test the walk over the source tree."""

    def test_deep_tree_needs_no_deep_stack(self, tmp_path: Path) -> None:
        """Test that nesting depth costs heap, not C stack."""
        # Shallow enough that the full path stays under Windows' MAX_PATH
        deepest = tmp_path / "t" / Path(*["d"] * 60)
        deepest.mkdir(parents=True)
        (deepest / "f").write_text("f")

        result = run_child(
            _ARCHIVE_ON_SMALL_STACK, str(tmp_path / "out.tar"), str(tmp_path / "t")
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "62"

    def test_entries_follow_their_directory(self, tmp_path: Path) -> None:
        """Test that each directory's contents come straight after it."""
        for rel in ("t/a/x", "t/a/y/z", "t/b"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(rel)
        output = tmp_path / "out.tar"

        _core.create_archive(str(output), "tar", [str(tmp_path / "t")])

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert sorted(listed) == sorted(
            ["t/", "t/a/", "t/a/x", "t/a/y/", "t/a/y/z", "t/b"]
        )
        for i, path in enumerate(listed):
            if path.endswith("/"):
                inside = [p for p in listed if p.startswith(path) and p != path]
                assert listed[i + 1 : i + 1 + len(inside)] == inside, listed


class TestSizeChange:
    """Test a file whose size changes between the walk and its copy."""

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    @pytest.mark.parametrize(
        ("change", "fill", "message"),
        [
            ("grow", b"x", "file grew while being archived"),
            ("shrink", b"\0", "file shrank by"),
        ],
    )
    def test_warns_and_keeps_the_walked_size(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        fmt: str,
        change: str,
        fill: bytes,
        message: str,
    ) -> None:
        """Test that the entry keeps its walked size, with a warning."""
        first = tmp_path / "a.bin"
        first.write_bytes(b"a" * (4 << 20))
        source = tmp_path / "f.bin"
        source.write_bytes(b"x" * (4 << 20))
        output = tmp_path / f"out.{fmt}"
        changed = False

        def change_source(done: int, total: int) -> None:
            # The first report comes while a.bin is still being read
            nonlocal changed
            if not changed:
                changed = True
                with source.open("r+b") as f:
                    if change == "grow":
                        f.seek(0, os.SEEK_END)
                        f.write(b"y" * (1 << 20))
                    else:
                        f.truncate(0)

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.create_archive(
                str(output), fmt, [str(first), str(source)], progress=change_source
            )

        if fmt == "zip":
            with zipfile.ZipFile(output) as zf:
                data = zf.read("f.bin")
        else:
            with tarfile.open(output) as tf:
                f = tf.extractfile("f.bin")
                assert f is not None
                data = f.read()
        assert data == fill * (4 << 20)
        assert f"{source}: {message}" in caplog.text

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_file_removed_before_its_read(self, tmp_path: Path, fmt: str) -> None:
        """Test that a file gone by its turn names itself, and no output is left."""
        first = tmp_path / "a.bin"
        first.write_bytes(b"a" * (4 << 20))
        source = tmp_path / "f.bin"
        source.write_bytes(b"x")
        output = tmp_path / f"out.{fmt}"

        def remove_source(done: int, total: int) -> None:
            source.unlink(missing_ok=True)

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(
                str(output), fmt, [str(first), str(source)], progress=remove_source
            )

        assert info.value.filename == str(source)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.bin"]


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

    def test_creation_keeps_a_name_the_locale_cant_convert(
        self, tmp_path: Path
    ) -> None:
        """Test that a non-ASCII name under the C locale is archived, as bsdtar does."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "café").write_text("x")
        output = tmp_path / "out.tar"
        # UTF-8 mode without coercion keeps libarchive's locale at plain C
        env = {
            **os.environ,
            "LC_ALL": "C",
            "PYTHONUTF8": "1",
            "PYTHONCOERCECLOCALE": "0",
        }

        result = run_child(_ARCHIVE_AND_LIST, str(output), "tar", str(tree), env=env)

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == ["tree/", "tree/café"]


class TestTarUtf8Names:
    """Test that tar names reach the archive as UTF-8, as pax requires."""

    def test_names_outside_the_ansi_code_page_round_trip(self, tmp_path: Path) -> None:
        """Test that a name and symlink target outside ASCII and cp1252 survive."""
        name = "日本-ж"
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / name).write_text("x")
        has_symlink = sys.platform != "win32"
        if has_symlink:
            os.symlink(name, tree / f"{name}-sym")
        names = sorted(os.listdir(tree))
        output = tmp_path / "out.tar"

        _core.create_archive(str(output), "tar", [str(tree)])

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert sorted(listed) == ["tree/"] + [f"tree/{n}" for n in names]
        with tarfile.open(output) as tf:
            if has_symlink:
                assert tf.getmember(f"tree/{name}-sym").linkname == name
        _core.extract_archive(str(output), str(tmp_path / "out"), [], allow_symlinks=1)
        assert sorted(os.listdir(tmp_path / "out" / "tree")) == names


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

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
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


class TestHardlinkCreation:
    """Test that tar stores a file's later names as links to its first."""

    def _round_trip(self, tmp_path: Path, make: Callable[[Path], object]) -> Path:
        """Archive src/a and its hardlink src/b, check the listing, and extract."""
        src = tmp_path / "src"
        src.mkdir()
        make(src / "a")
        os.link(src / "a", src / "b")
        archive = tmp_path / "a.tar"

        _core.create_archive(str(archive), "tar", [str(src)])

        entries = {e["path"]: e for e in _core.list_archive_contents(str(archive))}
        first, later = sorted(["src/a", "src/b"], key=list(entries).index)
        assert entries[later]["type"] == "hardlink"
        assert entries[later]["link_target"] == first
        out = tmp_path / "out"
        _core.extract_archive(str(archive), str(out), [])
        return out / "src"

    def test_file_is_stored_once(self, tmp_path: Path) -> None:
        """Test that the extracted names share an inode."""
        out = self._round_trip(tmp_path, lambda p: p.write_text("x"))

        assert (out / "a").read_text() == "x"
        assert (out / "a").stat().st_ino == (out / "b").stat().st_ino

    @pytest.mark.skipif(sys.platform == "win32", reason="no FIFOs on Windows")
    def test_fifo_is_linked(self, tmp_path: Path) -> None:
        """Test that a link naming a FIFO links it, as bsdtar does."""
        out = self._round_trip(tmp_path, os.mkfifo)

        assert stat.S_ISFIFO((out / "b").lstat().st_mode)
        assert (out / "a").lstat().st_ino == (out / "b").lstat().st_ino

    def test_zip_keeps_copies(self, tmp_path: Path) -> None:
        """Test that zip stores each name in full, as Info-ZIP does."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a").write_text("x")
        os.link(src / "a", src / "b")
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            assert zf.read("src/b") == b"x"


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
    def test_modes_are_stored(self, tmp_path: Path) -> None:
        """Test that each entry's mode lands in the attributes, typed."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "run").write_bytes(b"x")
        (src / "run").chmod(0o750)
        src.chmod(0o750)
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            modes = {i.filename: i.external_attr >> 16 for i in zf.infolist()}
        assert modes["src/run"] == 0o100750
        assert modes["src/"] == 0o040750

    @pytest.mark.usefixtures("needs_symlinks")
    def test_link_is_stored_as_a_link(self, tmp_path: Path) -> None:
        """Test that a symlink is its target text, marked S_IFLNK."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "link").symlink_to("target")
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            info = zf.getinfo("src/link")
            assert stat.S_ISLNK(info.external_attr >> 16)
            assert zf.read(info) == b"target"

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


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
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
        self, tmp_path: Path, fmt: str
    ) -> None:
        """Test that corrupt input onto an existing file neither truncates nor removes it."""
        source = tmp_path / "source.bin"
        source.write_bytes(os.urandom(64 * 1024))
        compressed = tmp_path / f"source.{fmt}"
        _compress_to(fmt, source, compressed)
        data = compressed.read_bytes()
        compressed.write_bytes(data[: len(data) // 2])

        existing = tmp_path / "existing.bin"
        existing.write_bytes(b"keep me")

        with pytest.raises(CorruptDataError):
            decompress_file(str(compressed), str(existing), "", overwrite=2)

        assert existing.read_bytes() == b"keep me"
        assert not list(tmp_path.glob(".compresso-*"))

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    def test_failed_archive_creation_keeps_the_existing_file(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that an archive that fails part way never replaces the one there."""
        existing = tmp_path / f"existing.{fmt}"
        existing.write_bytes(b"keep me")

        with pytest.raises(FileNotFoundError):
            _core.create_archive(
                str(existing),
                fmt,
                [str(sample_text_file), str(tmp_path / "missing")],
                overwrite=2,
            )

        assert existing.read_bytes() == b"keep me"
        assert not list(tmp_path.glob(".compresso-*"))

    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    def test_overwriting_the_input_itself_is_refused(
        self, sample_text_file: Path, fmt: str
    ) -> None:
        """Test that an output that is the input file is refused before any work."""
        original = sample_text_file.read_bytes()

        with pytest.raises(ValueError, match="same file"):
            _compress_to(fmt, sample_text_file, sample_text_file, overwrite=2)

        assert sample_text_file.read_bytes() == original


class TestOutputsKeepSourceMetadata:
    """Test that single-file outputs take the source's mode and mtime."""

    MTIME = 1_600_000_000

    @staticmethod
    def _round_trip(fmt: str, source: Path) -> tuple[Path, Path]:
        """Compress `source` beside itself and decompress it again.

        Returns:
            The compressed file and the restored one.
        """
        compressed = source.with_name(f"{source.name}.{fmt}")
        _compress_to(fmt, source, compressed)
        restored = source.with_name("restored")
        decompress_file(str(compressed), str(restored), "")
        return compressed, restored

    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    def test_mtime_carries_through_both_ways(self, tmp_path: Path, fmt: str) -> None:
        """Test that the compressed file, then the restored one, keep the date."""
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello" * 100)
        os.utime(source, (self.MTIME, self.MTIME))

        for output in self._round_trip(fmt, source):
            assert int(output.stat().st_mtime) == self.MTIME

    @pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX modes")
    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [(0o640, 0o640), (0o777, 0o777), (0o4755, 0o755)],
        ids=["private", "no-umask", "setuid"],
    )
    def test_mode_carries_through_without_setuid(
        self, tmp_path: Path, fmt: str, mode: int, expected: int
    ) -> None:
        """Test the permission bits are copied as is, setuid aside, both ways."""
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello" * 100)
        source.chmod(mode)

        for output in self._round_trip(fmt, source):
            assert stat.S_IMODE(output.stat().st_mode) == expected


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
        tmp_path: Path,
        algo: str,
        strategy: str,
        level: int,
        match: str,
    ) -> None:
        """Test that an out-of-range level is refused before anything is written."""
        dest = tmp_path / "out.comp"
        with pytest.raises(ValueError, match=match):
            compress_file(str(sample_text_file), str(dest), algo, strategy, level)
        assert not dest.exists()

    @pytest.mark.parametrize(
        "algo,level",
        [("zlib", 9), ("bzip2", 1), ("zstd", 22), ("lz4", 12), ("snappy", -1)],
    )
    def test_compress_file_accepts_range_ends(
        self, sample_text_file: Path, tmp_path: Path, algo: str, level: int
    ) -> None:
        """Test that each end of a backend's range, and the default, still work."""
        compress_file(
            str(sample_text_file), str(tmp_path / "out.comp"), algo, "", level
        )

    @pytest.mark.parametrize(
        "fmt,level,match",
        [
            ("gz", 10, r"gzip compression level 10 out of range \(0-9"),
            ("zst", 23, r"zstd compression level 23 out of range \(1-22"),
            ("sz", 1, "snappy has no compression levels"),
        ],
    )
    def test_compress_standalone_refuses_out_of_range(
        self, sample_text_file: Path, tmp_path: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that the standalone formats check the level too."""
        with pytest.raises(ValueError, match=match):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / f"out.{fmt}"), fmt, level
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
        self, sample_text_file: Path, tmp_path: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that archives check the level of whichever stage compresses."""
        dest = tmp_path / f"out.{fmt}"
        with pytest.raises(ValueError, match=match):
            _core.create_archive(str(dest), fmt, [str(sample_text_file)], level)
        assert not dest.exists()

    def test_create_archive_accepts_a_codec_stage_level(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that tar.zst takes zstd's levels even though tar has none."""
        _core.create_archive(
            str(tmp_path / "out.tar.zst"), "tar.zst", [str(sample_text_file)], 19
        )

    @pytest.mark.parametrize(
        "algo,level",
        [("zlib", 9), ("zlib", 10), ("bzip2", 0), ("zstd", 22), ("snappy", 1)],
    )
    def test_check_level_agrees_with_compress_file(
        self, sample_text_file: Path, tmp_path: Path, algo: str, level: int
    ) -> None:
        """Test that the up-front check refuses exactly what compression refuses."""

        checked = _value_error(lambda: _core.check_level(level, algo=algo))
        compressed = _value_error(
            lambda: compress_file(
                str(sample_text_file), str(tmp_path / "o.comp"), algo, "", level
            )
        )
        assert checked == compressed

    @pytest.mark.parametrize(
        "fmt,level", [("gz", 10), ("tar", 1), ("tar.zst", 23), ("zip", 9)]
    )
    def test_check_level_agrees_with_create_archive(
        self, sample_text_file: Path, tmp_path: Path, fmt: str, level: int
    ) -> None:
        """Test that the up-front check matches archive and standalone creation."""

        checked = _value_error(lambda: _core.check_level(level, format=fmt))
        dest = str(tmp_path / f"o.{fmt}")
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


@pytest.fixture(scope="module")
def several_blocks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compressible input that spans several zstd jobs and xz blocks at level 1.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        The path to the created file.
    """
    rnd = random.Random(0)
    path = tmp_path_factory.mktemp("threads") / "input.bin"
    path.write_bytes(b"".join(rnd.randbytes(4096) * 4 for _ in range(512)))
    return path


class TestCodecThreads:
    """Test the threads= argument and the settings that came with it."""

    @pytest.mark.parametrize("fmt", ["zstd", "xz"])
    def test_output_is_the_same_for_any_thread_count(
        self, several_blocks: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a file doesn't depend on the machine that wrote it."""
        outputs = []
        for threads in (1, 3):
            out = tmp_path / f"{threads}.{fmt}"
            _core.compress_standalone(
                str(several_blocks), str(out), fmt, 1, threads=threads
            )
            outputs.append(out)

        assert outputs[0].read_bytes() == outputs[1].read_bytes()

        restored = tmp_path / "restored"
        _core.decompress_standalone(str(outputs[1]), str(restored), fmt, threads=3)
        assert restored.read_bytes() == several_blocks.read_bytes()

    def test_threaded_xz_is_read_by_the_stdlib(
        self, several_blocks: Path, tmp_path: Path
    ) -> None:
        """Test that the multi-block layout is ordinary .xz."""
        out = tmp_path / "out.xz"
        _core.compress_standalone(str(several_blocks), str(out), "xz", 1, threads=2)

        assert lzma.decompress(out.read_bytes()) == several_blocks.read_bytes()

    def test_zstd_frame_records_the_content_size(
        self, several_blocks: Path, tmp_path: Path
    ) -> None:
        """Test that a streamed input's size is pledged and written to the header."""
        out = tmp_path / "out.zst"
        _core.compress_standalone(str(several_blocks), str(out), "zstd", 1)

        descriptor = out.read_bytes()[4]
        # Frame_Content_Size_flag, or Single_Segment_flag, which implies the field
        assert descriptor >> 6 or descriptor & 0x20

    @pytest.mark.parametrize("algo", ["lzma", "zstd"])
    def test_max_ratio_reaches_the_codec(
        self, several_blocks: Path, tmp_path: Path, algo: str
    ) -> None:
        """Test that max_ratio adds lzma's extreme preset and zstd's long matching."""
        outputs = {}
        for strategy in ("balanced", "max_ratio"):
            out = tmp_path / f"{strategy}.comp"
            compress_file(str(several_blocks), str(out), algo, strategy, 1)
            outputs[strategy] = out

            restored = tmp_path / f"{strategy}.out"
            decompress_file(str(out), str(restored), "")
            assert restored.read_bytes() == several_blocks.read_bytes()

        assert outputs["balanced"].read_bytes() != outputs["max_ratio"].read_bytes()

    def test_negative_count_is_refused(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that threads below 0 raise rather than meaning one per core."""
        with pytest.raises(ValueError, match="threads must be 0"):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / "out.zst"), "zstd", threads=-1
            )
        assert not (tmp_path / "out.zst").exists()

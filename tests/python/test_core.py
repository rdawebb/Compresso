"""Tests for the core compression/decompression functionality."""

import importlib.util
import lzma
import os
import random
import stat
import sys
from collections.abc import Callable
from pathlib import Path

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

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [("tar.gz", True), ("tgz", True), ("zip", True), ("zip.gz", False)],
    )
    def test_only_a_valid_pipeline_is_an_archive(
        self, fmt: str, expected: bool
    ) -> None:
        """Test that zip, which compresses its own entries, takes no codec."""
        assert _core.format_is_archive(fmt) is expected


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

    @pytest.mark.parametrize(
        "source", ["empty_file", "small_file", "sample_binary_file"]
    )
    def test_round_trip_every_backend(
        self,
        request: pytest.FixtureRequest,
        tmp_path: Path,
        compression_algo: str,
        source: str,
    ) -> None:
        """Test that each backend's output decompresses back to the original bytes."""
        original: Path = request.getfixturevalue(source)
        compressed = tmp_path / f"{compression_algo}.comp"
        restored = tmp_path / f"{compression_algo}.out"

        written = compress_file(
            str(original), str(compressed), compression_algo, "balanced", -1
        )
        assert written == str(compressed)

        assert decompress_file(str(compressed), str(restored), "") == str(restored)
        assert restored.read_bytes() == original.read_bytes()


class TestDecompressFile:
    """Test the decompress_file function."""

    def test_decompress_invalid_file(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a file in no recognised format is refused as such."""
        output_file = tmp_path / "output.txt"

        with pytest.raises(Error, match="Unknown or unsupported format"):
            decompress_file(str(sample_text_file), str(output_file), "")

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

    @pytest.mark.parametrize(
        ("fmt", "engine"), [("gz", "gzip"), ("comp", "zlib")], ids=["gzip", "comp"]
    )
    @pytest.mark.parametrize(("algo", "refused"), [("zstd", True), ("zlib", False)])
    def test_algo_is_checked_against_the_input(
        self, tmp_path: Path, fmt: str, engine: str, algo: str, refused: bool
    ) -> None:
        """Test that an explicit algo must match the engine the input records."""
        source = tmp_path / "source"
        source.write_bytes(b"payload")
        compressed = tmp_path / f"data.{fmt}"
        _compress_to(fmt, source, compressed)
        output = tmp_path / "data"

        message = _value_error(lambda: decompress_file(compressed, output, algo))

        if refused:
            assert (
                message == f"File was compressed with {engine}, not the requested zstd"
            )
            assert not output.exists()
        else:
            assert message is None
            assert output.read_bytes() == b"payload"


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

    @pytest.mark.parametrize("fmt", ["bogus", "comp", ""])
    def test_unknown_format_is_refused(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a name that resolves to no format at all is refused earlier."""
        with pytest.raises(ValueError):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / "out.bin"), fmt, 3
            )


_MISSING = ("missing", FileNotFoundError)
# Windows refuses to open a directory at all, so it reports EACCES
_DIRECTORY = (".", (IsADirectoryError, PermissionError))


class TestUnreadableInput:
    """Test that an input the OS can't read is an OSError, not an unknown format."""

    @pytest.mark.parametrize(
        ("call", "name", "raises"),
        [
            pytest.param(
                lambda src, out: compress_file(src, out, "zlib", "balanced", 6),
                *_MISSING,
                id="compress_file",
            ),
            pytest.param(
                lambda src, out: decompress_file(src, out, ""),
                *_MISSING,
                id="decompress_file",
            ),
            pytest.param(
                lambda src, out: _core.decompress_standalone(src, out),
                *_MISSING,
                id="decompress_standalone",
            ),
            pytest.param(
                lambda src, out: _core.decompress_standalone(src, out, "gzip"),
                *_MISSING,
                id="decompress_standalone_gzip",
            ),
            pytest.param(
                lambda src, _: _core.detect_format(src), *_MISSING, id="detect_format"
            ),
            pytest.param(
                lambda src, _: _core.list_archive_contents(src),
                *_MISSING,
                id="list",
            ),
            pytest.param(
                lambda src, out: _core.extract_archive(src, out, []),
                *_MISSING,
                id="extract",
            ),
            pytest.param(
                lambda src, out: decompress_file(src, out, ""),
                *_DIRECTORY,
                id="decompress_file_directory",
            ),
            pytest.param(
                lambda src, _: _core.list_archive_contents(src),
                *_DIRECTORY,
                id="list_directory",
            ),
            pytest.param(
                lambda src, out: _core.extract_archive(src, out, []),
                *_DIRECTORY,
                id="extract_directory",
            ),
        ],
    )
    def test_unreadable_input_is_an_os_error(
        self,
        tmp_path: Path,
        call: Callable[[str, str], object],
        name: str,
        raises: type[OSError] | tuple[type[OSError], ...],
    ) -> None:
        """Test that a missing input or a directory raises the OS's own error."""
        with pytest.raises(raises):
            call(str(tmp_path / name), str(tmp_path / "out"))


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

    def test_keyword_arguments_are_refused(self, tmp_path: Path) -> None:
        """Test that a stray keyword to list_archive_contents raises, not drops."""
        with pytest.raises(TypeError, match="keyword"):
            _core.list_archive_contents(str(tmp_path / "a.tar"), bogus=1)  # type: ignore[call-arg]  # ty:ignore[unknown-argument]


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
            # Checked before the input is opened, so the paths don't need to exist
            lambda: compress_file("missing", "out.comp", "", "fsat", -1),
            lambda: _core.get_default_backend_for_strategy("fsat"),
            lambda: _core.check_level(5, strategy="fsat"),
            # An explicit algorithm overrides the strategy, but a typo is still one
            lambda: _core.check_level(5, algo="zstd", strategy="fsat"),
        ],
        ids=[
            "compress_file",
            "default_backend",
            "check_level",
            "check_level_with_algo",
        ],
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

    def test_compress_standalone_refuses_out_of_range(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that the standalone formats check the level too."""
        with pytest.raises(ValueError, match=r"gzip compression level 10 out of range"):
            _core.compress_standalone(
                str(sample_text_file), str(tmp_path / "out.gz"), "gz", 10
            )

    def test_create_archive_refuses_out_of_range(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that an archive checks the codec stage's range, not the container's."""
        dest = tmp_path / "out.tar.zst"
        with pytest.raises(ValueError, match=r"zstd compression level 23 out of range"):
            _core.create_archive(str(dest), "tar.zst", [str(sample_text_file)], 23)
        assert not dest.exists()

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
    """Compressible input that spans several zstd jobs and xz blocks at level 1."""
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

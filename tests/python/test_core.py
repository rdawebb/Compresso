"""Tests for the core compression/decompression functionality."""

import sys
from pathlib import Path

import pytest

from compresso import (
    BackendError,
    Error,
    HeaderError,
    _core,
    compress_file,
    decompress_file,
)
from compresso._core import get_capabilities


class TestCoreExceptions:
    """Test custom exception classes."""

    def test_error_inheritance(self) -> None:
        """Test that Error is a subclass of Exception."""
        assert issubclass(Error, Exception)

    def test_header_error_inheritance(self) -> None:
        """Test that HeaderError is a subclass of Error."""
        assert issubclass(HeaderError, Error)

    def test_backend_error_inheritance(self) -> None:
        """Test that BackendError is a subclass of Error."""
        assert issubclass(BackendError, Error)

    def test_error_instantiation(self) -> None:
        """Test that custom exceptions can be instantiated."""
        err = Error("test message")
        assert str(err) == "test message"

        header_err = HeaderError("header issue")
        assert str(header_err) == "header issue"

        backend_err = BackendError("backend issue")
        assert str(backend_err) == "backend issue"


class TestCapabilities:
    """Test the get_capabilities function."""

    def test_get_capabilities_returns_list(self) -> None:
        """Test that get_capabilities returns a list."""
        caps = get_capabilities()
        assert isinstance(caps, list)

    def test_get_capabilities_not_empty(self) -> None:
        """Test that capabilities list is not empty."""
        caps = get_capabilities()
        assert len(caps) > 0

    def test_capabilities_structure(self) -> None:
        """Test that each capability has the expected structure."""
        caps = get_capabilities()
        for cap in caps:
            # Unregistered backend slots are None; the rest match the stub
            if cap is None:
                continue
            assert set(cap) == {"name", "id"}
            assert isinstance(cap["name"], str)
            assert isinstance(cap["id"], int)

    def test_capabilities_have_known_algos(self) -> None:
        """Test that common algorithms are present."""
        caps = get_capabilities()
        # zlib should always be available
        caps_str = str(caps).lower()
        assert "zlib" in caps_str


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

    def test_compress_file_basic(self, sample_text_file: Path, temp_dir: Path) -> None:
        """Test basic file compression."""
        output_file = temp_dir / "compressed.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", "balanced", 6
        )

        assert result == str(output_file)
        assert output_file.exists()
        assert output_file.stat().st_size > 0

    @pytest.mark.parametrize("algo", ["zlib", "zstd", "lz4"])
    def test_compress_with_different_algorithms(
        self, sample_text_file: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test compression with different algorithms."""
        output_file = temp_dir / f"compressed_{algo}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), algo, "balanced", 6
        )

        assert result == str(output_file)
        assert output_file.exists()

    @pytest.mark.parametrize("strategy", ["fast", "balanced", "max_ratio"])
    def test_compress_with_different_strategies(
        self, sample_text_file: Path, temp_dir: Path, strategy: str
    ) -> None:
        """Test compression with different strategies."""
        output_file = temp_dir / f"compressed_{strategy}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", strategy, 6
        )

        assert result == str(output_file)
        assert output_file.exists()

    @pytest.mark.parametrize("level", [1, 3, 6, 9])
    def test_compress_with_different_levels(
        self, sample_text_file: Path, temp_dir: Path, level: int
    ) -> None:
        """Test compression with different levels."""
        output_file = temp_dir / f"compressed_level{level}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", "balanced", level
        )

        assert result == str(output_file)
        assert output_file.exists()

    def test_compress_nonexistent_file(self, temp_dir: Path) -> None:
        """Test compressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.txt"
        output_file = temp_dir / "output.comp"

        with pytest.raises((Error, OSError, FileNotFoundError)):
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
        assert output_file.exists()
        # Highly compressible content should be much smaller
        assert output_file.stat().st_size < large_compressible_file.stat().st_size / 10


class TestDecompressFile:
    """Test the decompress_file function."""

    def test_decompress_file_basic(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test basic file decompression."""
        compressed_file = temp_dir / "compressed.comp"
        decompressed_file = temp_dir / "decompressed.txt"

        # First compress
        compress_file(
            str(sample_text_file), str(compressed_file), "zlib", "balanced", 6
        )

        # Then decompress
        result = decompress_file(str(compressed_file), str(decompressed_file), "")

        assert result == 0
        assert decompressed_file.exists()
        assert decompressed_file.read_text() == sample_text_file.read_text()

    @pytest.mark.parametrize("algo", ["zlib", "zstd", "lz4"])
    def test_round_trip_compression(
        self, sample_text_file: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test compression and decompression round trip."""
        compressed_file = temp_dir / f"compressed_{algo}.comp"
        decompressed_file = temp_dir / f"decompressed_{algo}.txt"

        original_content = sample_text_file.read_text()

        # Compress
        compress_file(str(sample_text_file), str(compressed_file), algo, "balanced", 6)

        # Decompress
        decompress_file(str(compressed_file), str(decompressed_file), "")

        # Verify content matches
        assert decompressed_file.read_text() == original_content

    def test_decompress_nonexistent_file(self, temp_dir: Path) -> None:
        """Test decompressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.comp"
        output_file = temp_dir / "output.txt"

        with pytest.raises((Error, OSError, FileNotFoundError)):
            decompress_file(str(input_file), str(output_file), "")

    def test_decompress_invalid_file(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test decompressing an invalid compressed file."""
        output_file = temp_dir / "output.txt"

        with pytest.raises((Error, HeaderError)):
            decompress_file(str(sample_text_file), str(output_file), "")

    def test_round_trip_binary_file(
        self, sample_binary_file: Path, temp_dir: Path
    ) -> None:
        """Test compression and decompression of binary data."""
        compressed_file = temp_dir / "compressed.comp"
        decompressed_file = temp_dir / "decompressed.bin"

        original_content = sample_binary_file.read_bytes()

        # Compress
        compress_file(
            str(sample_binary_file), str(compressed_file), "zlib", "balanced", 6
        )

        # Decompress
        decompress_file(str(compressed_file), str(decompressed_file), "")

        # Verify binary content matches exactly
        assert decompressed_file.read_bytes() == original_content

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


class TestRecognisedButUnsupportedArchive:
    """Test that a detected archive format without a backend names itself."""

    @pytest.fixture
    def fake_7z(self, temp_dir: Path) -> Path:
        """A file that detects as 7z by its magic bytes alone."""
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

"""Tests for the core compression/decompression functionality."""

import pytest
from pathlib import Path

from compresso import (
    compress_file,
    decompress_file,
    Error,
    HeaderError,
    BackendError,
)
from compresso._core import get_capabilities


class TestCoreExceptions:
    """Test custom exception classes."""

    def test_error_inheritance(self):
        """Test that Error is a subclass of Exception."""
        assert issubclass(Error, Exception)

    def test_header_error_inheritance(self):
        """Test that HeaderError is a subclass of Error."""
        assert issubclass(HeaderError, Error)

    def test_backend_error_inheritance(self):
        """Test that BackendError is a subclass of Error."""
        assert issubclass(BackendError, Error)

    def test_error_instantiation(self):
        """Test that custom exceptions can be instantiated."""
        err = Error("test message")
        assert str(err) == "test message"

        header_err = HeaderError("header issue")
        assert str(header_err) == "header issue"

        backend_err = BackendError("backend issue")
        assert str(backend_err) == "backend issue"


class TestCapabilities:
    """Test the get_capabilities function."""

    def test_get_capabilities_returns_list(self):
        """Test that get_capabilities returns a list."""
        caps = get_capabilities()
        assert isinstance(caps, list)

    def test_get_capabilities_not_empty(self):
        """Test that capabilities list is not empty."""
        caps = get_capabilities()
        assert len(caps) > 0

    def test_capabilities_structure(self):
        """Test that each capability has the expected structure."""
        caps = get_capabilities()
        for cap in caps:
            # Each capability should be iterable
            assert hasattr(cap, "__iter__")

    def test_capabilities_have_known_algos(self):
        """Test that common algorithms are present."""
        caps = get_capabilities()
        # zlib should always be available
        caps_str = str(caps).lower()
        assert "zlib" in caps_str


class TestCompressFile:
    """Test the compress_file function."""

    def test_compress_file_basic(self, sample_text_file: Path, temp_dir: Path):
        """Test basic file compression."""
        output_file = temp_dir / "compressed.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", "balanced", 6
        )

        assert result == 0
        assert output_file.exists()
        assert output_file.stat().st_size > 0

    @pytest.mark.parametrize("algo", ["zlib", "zstd", "lz4"])
    def test_compress_with_different_algorithms(
        self, sample_text_file: Path, temp_dir: Path, algo: str
    ):
        """Test compression with different algorithms."""
        output_file = temp_dir / f"compressed_{algo}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), algo, "balanced", 6
        )

        assert result == 0
        assert output_file.exists()

    @pytest.mark.parametrize("strategy", ["fast", "balanced", "max_ratio"])
    def test_compress_with_different_strategies(
        self, sample_text_file: Path, temp_dir: Path, strategy: str
    ):
        """Test compression with different strategies."""
        output_file = temp_dir / f"compressed_{strategy}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", strategy, 6
        )

        assert result == 0
        assert output_file.exists()

    @pytest.mark.parametrize("level", [1, 3, 6, 9])
    def test_compress_with_different_levels(
        self, sample_text_file: Path, temp_dir: Path, level: int
    ):
        """Test compression with different levels."""
        output_file = temp_dir / f"compressed_level{level}.comp"

        result = compress_file(
            str(sample_text_file), str(output_file), "zlib", "balanced", level
        )

        assert result == 0
        assert output_file.exists()

    def test_compress_nonexistent_file(self, temp_dir: Path):
        """Test compressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.txt"
        output_file = temp_dir / "output.comp"

        with pytest.raises((Error, OSError, FileNotFoundError)):
            compress_file(str(input_file), str(output_file), "zlib", "balanced", 6)

    def test_compress_empty_file(self, empty_file: Path, temp_dir: Path):
        """Test compressing an empty file."""
        output_file = temp_dir / "compressed_empty.comp"

        with pytest.raises(ValueError):
            compress_file(str(empty_file), str(output_file), "zlib", "balanced", 6)

    def test_compress_large_file(self, large_compressible_file: Path, temp_dir: Path):
        """Test compressing a large file."""
        output_file = temp_dir / "compressed_large.comp"

        result = compress_file(
            str(large_compressible_file), str(output_file), "zlib", "balanced", 6
        )

        assert result == 0
        assert output_file.exists()
        # Highly compressible content should be much smaller
        assert output_file.stat().st_size < large_compressible_file.stat().st_size / 10


class TestDecompressFile:
    """Test the decompress_file function."""

    def test_decompress_file_basic(self, sample_text_file: Path, temp_dir: Path):
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
    ):
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

    def test_decompress_nonexistent_file(self, temp_dir: Path):
        """Test decompressing a file that doesn't exist."""
        input_file = temp_dir / "nonexistent.comp"
        output_file = temp_dir / "output.txt"

        with pytest.raises((Error, OSError, FileNotFoundError)):
            decompress_file(str(input_file), str(output_file), "")

    def test_decompress_invalid_file(self, sample_text_file: Path, temp_dir: Path):
        """Test decompressing an invalid compressed file."""
        output_file = temp_dir / "output.txt"

        with pytest.raises((Error, HeaderError)):
            decompress_file(str(sample_text_file), str(output_file), "")

    def test_round_trip_binary_file(self, sample_binary_file: Path, temp_dir: Path):
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

    def test_round_trip_small_file(self, small_file: Path, temp_dir: Path):
        """Test compression and decompression of very small files."""
        compressed_file = temp_dir / "compressed_small.comp"
        decompressed_file = temp_dir / "decompressed_small.txt"

        original_content = small_file.read_text()

        compress_file(str(small_file), str(compressed_file), "zlib", "balanced", 6)

        decompress_file(str(compressed_file), str(decompressed_file), "")

        assert decompressed_file.read_text() == original_content

"""Tests for the file_inspect module."""

from pathlib import Path

from compresso import compress_file
from compresso.introspect.file_inspect import (
    COMP_HEADER_STRUCT,
    inspect,
)


class TestHeaderStruct:
    """Test the COMP_HEADER_STRUCT."""

    def test_header_struct_size(self) -> None:
        """Test that header struct has correct size."""
        # magic(4) + version(1) + algo(1) + level(1) + flags(1) + orig_size(8) = 16
        assert COMP_HEADER_STRUCT.size == 16


class TestInspect:
    """Test the inspect function."""

    def test_inspect_nonexistent_file(self, temp_dir: Path) -> None:
        """Test inspecting a file that doesn't exist."""
        file_path = temp_dir / "nonexistent.comp"
        result = inspect(file_path)

        assert result.is_compresso is False
        assert result.header_ok is False
        assert result.reason is not None
        assert result.can_decompress is False

    def test_inspect_empty_file(self, empty_file: Path) -> None:
        """Test inspecting an empty file."""
        result = inspect(empty_file)

        assert result.is_compresso is False
        assert result.header_ok is False
        assert result.can_decompress is False

    def test_inspect_text_file(self, sample_text_file: Path) -> None:
        """Test inspecting a regular text file (not compressed)."""
        result = inspect(sample_text_file)

        assert result.is_compresso is False
        assert result.header_ok is False
        assert result.can_decompress is False

    def test_inspect_valid_compressed_file(
        self, sample_text_file: Path, temp_dir: Path
    ):
        """Test inspecting a valid Compresso compressed file."""
        compressed_file = temp_dir / "compressed.comp"

        # First compress a file
        compress_file(
            str(sample_text_file), str(compressed_file), "zlib", "balanced", 6
        )

        # Then inspect it
        result = inspect(compressed_file)

        assert result.is_compresso is True
        assert result.header_ok is True
        assert result.algo_name is not None
        assert result.orig_size is not None
        assert result.version is not None

    def test_inspect_path_as_string(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test inspect with path as string."""
        compressed_file = temp_dir / "compressed.comp"

        compress_file(
            str(sample_text_file), str(compressed_file), "zlib", "balanced", 6
        )

        # Inspect using string path
        result = inspect(str(compressed_file))

        assert isinstance(result.path, Path)
        assert result.is_compresso is True

    def test_inspect_file_too_small(self, temp_dir: Path) -> None:
        """Test inspecting a file that's too small to be valid."""
        small_file = temp_dir / "small.comp"
        small_file.write_bytes(b"COMP")  # Only 4 bytes, needs 16

        result = inspect(small_file)

        assert result.is_compresso is False
        assert result.header_ok is False

    def test_inspect_invalid_magic(self, temp_dir: Path) -> None:
        """Test inspecting a file with invalid magic bytes."""
        invalid_file = temp_dir / "invalid.comp"
        # Write 16 bytes but with wrong magic
        invalid_file.write_bytes(b"XXXX" + b"\x00" * 12)

        result = inspect(invalid_file)

        assert result.is_compresso is False
        assert result.header_ok is False

    def test_inspect_result_fields_when_invalid(self, sample_text_file: Path) -> None:
        """Test that inspect result has None fields when file is invalid."""
        result = inspect(sample_text_file)

        assert result.version is None
        assert result.algo_id is None
        assert result.algo_name is None
        assert result.level is None
        assert result.orig_size is None
        assert result.estimated_decomp_s is None

"""Tests for the file_inspect module."""

from pathlib import Path

import pytest

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

    @pytest.mark.parametrize(
        "data",
        [
            None,
            b"",
            b"Hello, World! " * 100,
            b"COMP",  # Too short for the 16-byte header
            b"XXXX" + bytes(12),
        ],
        ids=["missing", "empty", "text", "too-small", "bad-magic"],
    )
    def test_not_a_compresso_file(self, tmp_path: Path, data: bytes | None) -> None:
        """Test that anything without a valid header is refused, its fields left unset."""
        path = tmp_path / "input.comp"
        if data is not None:
            path.write_bytes(data)

        result = inspect(path)

        assert result.is_compresso is False
        assert result.header_ok is False
        assert result.can_decompress is False
        assert result.version is None
        assert result.algo_id is None
        assert result.algo_name is None
        assert result.level is None
        assert result.orig_size is None
        assert result.estimated_decomp_s is None
        if data is None:
            assert result.reason is not None

    def test_inspect_valid_compressed_file(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test inspecting a valid Compresso file, given by a str path."""
        compressed_file = tmp_path / "compressed.comp"
        compress_file(
            str(sample_text_file), str(compressed_file), "zlib", "balanced", 6
        )

        result = inspect(str(compressed_file))

        assert isinstance(result.path, Path)
        assert result.is_compresso is True
        assert result.header_ok is True
        assert result.algo_name is not None
        assert result.orig_size is not None
        assert result.version is not None

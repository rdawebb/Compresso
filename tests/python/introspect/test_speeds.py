"""Tests for the speeds module."""

from pathlib import Path

import pytest

from compresso.introspect.speeds import (
    update_from_benchmarks,
)


class TestGetEstimatedSpeeds:
    """Test the get_estimated_speeds function."""

    def test_get_estimated_speeds_decompress(self) -> None:
        """Test getting estimated decompression speed."""
        from compresso.introspect.speeds import get_estimated_speeds

        speed = get_estimated_speeds("zlib", operation="decompress")
        assert speed > 0
        assert isinstance(speed, float)

    def test_get_estimated_speeds_compress(self) -> None:
        """Test getting estimated compression speed."""
        from compresso.introspect.speeds import get_estimated_speeds

        speed = get_estimated_speeds("zlib", operation="compress")
        assert speed > 0
        assert isinstance(speed, float)

    @pytest.mark.parametrize("algo", ["zlib", "bzip2", "lzma", "zstd", "lz4", "snappy"])
    def test_get_estimated_speeds_common_algos(self, algo: str) -> None:
        """Test that common algorithms have speed estimates."""
        from compresso.introspect.speeds import get_estimated_speeds

        decomp_speed = get_estimated_speeds(algo, operation="decompress")
        comp_speed = get_estimated_speeds(algo, operation="compress")

        # All algorithms should have positive speeds
        assert decomp_speed > 0
        assert comp_speed > 0

    def test_get_estimated_speeds_consistency(self) -> None:
        """Test that speeds are consistent across calls."""
        from compresso.introspect.speeds import get_estimated_speeds

        speed1 = get_estimated_speeds("zlib")
        speed2 = get_estimated_speeds("zlib")

        # Should return the same values
        assert speed1 == speed2

    def test_get_estimated_speeds_default_operation(self) -> None:
        """Test that default operation is decompress."""
        from compresso.introspect.speeds import get_estimated_speeds

        default_speed = get_estimated_speeds("zlib")
        decomp_speed = get_estimated_speeds("zlib", operation="decompress")

        # Default should be same as explicit decompress
        assert default_speed == decomp_speed


class TestUpdateFromBenchmarks:
    """Test the update_from_benchmarks function."""

    def test_update_from_benchmarks_with_empty_list(self) -> None:
        """Test updating from empty benchmark list."""
        # Should handle empty list without error
        update_from_benchmarks([])

    def test_update_from_benchmarks_creates_file(self, speeds_file: Path) -> None:
        """Test that update_from_benchmarks creates config file."""
        from compresso.introspect.benchmark import BenchmarkResult

        results = [
            BenchmarkResult(
                algo="zlib",
                strategy="balanced",
                level=6,
                compress_time=1.0,
                decompress_time=0.5,
                input_size=1024 * 1024,
                compressed_size=500000,
            )
        ]

        update_from_benchmarks(results)

        # Check if config directory was created
        assert speeds_file.parent.exists()

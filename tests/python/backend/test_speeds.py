"""Tests for the speeds module."""

import pytest
from pathlib import Path
import json

from compresso.backend.speeds import (
    AlgoSpeeds,
    update_from_benchmarks,
)


class TestAlgoSpeeds:
    """Test the AlgoSpeeds dataclass."""

    def test_algo_speeds_creation(self):
        """Test creating an AlgoSpeeds instance."""
        speeds = AlgoSpeeds(algo="zlib", comp_mb_s=200.0, decomp_mb_s=250.0, samples=5)

        assert speeds.algo == "zlib"
        assert speeds.comp_mb_s == 200.0
        assert speeds.decomp_mb_s == 250.0
        assert speeds.samples == 5

    @pytest.mark.parametrize(
        "algo,comp_speed,decomp_speed",
        [
            ("zlib", 200.0, 250.0),
            ("zstd", 400.0, 500.0),
            ("lz4", 800.0, 900.0),
            ("bzip2", 50.0, 60.0),
        ],
    )
    def test_algo_speeds_various_algorithms(
        self, algo: str, comp_speed: float, decomp_speed: float
    ):
        """Test AlgoSpeeds with various algorithms."""
        speeds = AlgoSpeeds(
            algo=algo, comp_mb_s=comp_speed, decomp_mb_s=decomp_speed, samples=1
        )

        assert speeds.algo == algo
        assert speeds.comp_mb_s == comp_speed
        assert speeds.decomp_mb_s == decomp_speed


class TestGetEstimatedSpeeds:
    """Test the get_estimated_speeds function."""

    def test_get_estimated_speeds_decompress(self):
        """Test getting estimated decompression speed."""
        from compresso.backend.speeds import get_estimated_speeds

        speed = get_estimated_speeds("zlib", operation="decompress")
        assert speed > 0
        assert isinstance(speed, float)

    def test_get_estimated_speeds_compress(self):
        """Test getting estimated compression speed."""
        from compresso.backend.speeds import get_estimated_speeds

        speed = get_estimated_speeds("zlib", operation="compress")
        assert speed > 0
        assert isinstance(speed, float)

    @pytest.mark.parametrize("algo", ["zlib", "bzip2", "lzma", "zstd", "lz4", "snappy"])
    def test_get_estimated_speeds_common_algos(self, algo: str):
        """Test that common algorithms have speed estimates."""
        from compresso.backend.speeds import get_estimated_speeds

        decomp_speed = get_estimated_speeds(algo, operation="decompress")
        comp_speed = get_estimated_speeds(algo, operation="compress")

        # All algorithms should have positive speeds
        assert decomp_speed > 0
        assert comp_speed > 0

    def test_get_estimated_speeds_consistency(self):
        """Test that speeds are consistent across calls."""
        from compresso.backend.speeds import get_estimated_speeds

        speed1 = get_estimated_speeds("zlib")
        speed2 = get_estimated_speeds("zlib")

        # Should return the same values
        assert speed1 == speed2

    def test_get_estimated_speeds_default_operation(self):
        """Test that default operation is decompress."""
        from compresso.backend.speeds import get_estimated_speeds

        default_speed = get_estimated_speeds("zlib")
        decomp_speed = get_estimated_speeds("zlib", operation="decompress")

        # Default should be same as explicit decompress
        assert default_speed == decomp_speed


class TestUpdateFromBenchmarks:
    """Test the update_from_benchmarks function."""

    def test_update_from_benchmarks_exists(self):
        """Test that update_from_benchmarks function exists."""
        assert callable(update_from_benchmarks)

    def test_update_from_benchmarks_with_empty_list(self, mock_speeds_file: Path):
        """Test updating from empty benchmark list."""
        # Should handle empty list without error
        update_from_benchmarks([])

    def test_update_from_benchmarks_creates_file(
        self, mock_speeds_file: Path, temp_dir: Path
    ):
        """Test that update_from_benchmarks creates config file."""
        from compresso.backend.benchmark import BenchmarkResult

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
        config_dir = temp_dir / ".compresso"
        assert config_dir.exists()


class TestSpeedsFilePersistence:
    """Test speeds file reading and writing."""

    def test_speeds_file_format(self, temp_dir: Path):
        """Test that speeds file uses JSON format."""
        speeds_file = temp_dir / "speeds.json"

        test_data = {"zlib": {"comp_mb_s": 200.0, "decomp_mb_s": 250.0, "samples": 5}}

        speeds_file.write_text(json.dumps(test_data), "utf-8")

        # Verify it can be read as JSON
        loaded = json.loads(speeds_file.read_text("utf-8"))
        assert "zlib" in loaded
        assert loaded["zlib"]["comp_mb_s"] == 200.0

    def test_speeds_file_structure(self, temp_dir: Path):
        """Test expected structure of speeds file."""
        speeds_file = temp_dir / "speeds.json"

        test_data = {
            "zstd": {"comp_mb_s": 400.0, "decomp_mb_s": 500.0, "samples": 10},
            "lz4": {"comp_mb_s": 800.0, "decomp_mb_s": 900.0, "samples": 3},
        }

        speeds_file.write_text(json.dumps(test_data, indent=4), "utf-8")

        loaded = json.loads(speeds_file.read_text("utf-8"))

        # Check structure
        for algo_name, algo_data in loaded.items():
            assert "comp_mb_s" in algo_data
            assert "decomp_mb_s" in algo_data
            assert "samples" in algo_data
            assert isinstance(algo_data["comp_mb_s"], (int, float))
            assert isinstance(algo_data["decomp_mb_s"], (int, float))
            assert isinstance(algo_data["samples"], int)

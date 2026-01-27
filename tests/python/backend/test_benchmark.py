"""Tests for the benchmark module."""

import pytest

from compresso.backend.benchmark import (
    BenchmarkResult,
    benchmark_file,
    print_results,
)


class TestBenchmarkResult:
    """Test the BenchmarkResult dataclass."""

    def test_benchmark_result_creation(self):
        """Test creating a BenchmarkResult instance."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=1000000,
            compressed_size=500000,
        )

        assert result.algo == "zlib"
        assert result.strategy == "balanced"
        assert result.level == 6
        assert result.compress_time == 1.0
        assert result.decompress_time == 0.5
        assert result.input_size == 1000000
        assert result.compressed_size == 500000

    def test_ratio_property(self):
        """Test compression ratio calculation."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=1000000,
            compressed_size=500000,
        )

        assert result.ratio == 0.5

    def test_ratio_property_zero_input(self):
        """Test compression ratio with zero input size."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=0,
            compressed_size=100,
        )

        assert result.ratio == 0.0

    def test_comp_mb_s_property(self):
        """Test compression speed calculation."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=1024 * 1024,  # 1 MB
            compressed_size=500000,
        )

        # 1 MB in 1 second = 1 MB/s
        assert result.comp_mb_s == pytest.approx(1.0, rel=0.01)

    def test_comp_mb_s_property_zero_time(self):
        """Test compression speed with zero time."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=0.0,
            decompress_time=0.5,
            input_size=1024 * 1024,
            compressed_size=500000,
        )

        assert result.comp_mb_s == 0.0

    def test_decomp_mb_s_property(self):
        """Test decompression speed calculation."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=1024 * 1024 * 2,  # 2 MB
            compressed_size=500000,
        )

        # 2 MB in 0.5 seconds = 4 MB/s
        assert result.decomp_mb_s == pytest.approx(4.0, rel=0.01)

    @pytest.mark.parametrize(
        "algo,strategy,level",
        [
            ("zlib", "fast", 1),
            ("zstd", "balanced", 3),
            ("lz4", "fast", 1),
            ("bzip2", "max_ratio", 9),
        ],
    )
    def test_benchmark_result_with_various_params(
        self, algo: str, strategy: str, level: int
    ):
        """Test BenchmarkResult with various parameter combinations."""
        result = BenchmarkResult(
            algo=algo,
            strategy=strategy,
            level=level,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=100000,
            compressed_size=50000,
        )

        assert result.algo == algo
        assert result.strategy == strategy
        assert result.level == level


class TestBenchmarkFile:
    """Test the benchmark_file function."""

    def test_benchmark_file_exists(self):
        """Test that benchmark_file function exists."""
        assert callable(benchmark_file)

    def test_benchmark_file_signature(self):
        """Test benchmark_file function signature."""
        import inspect

        sig = inspect.signature(benchmark_file)
        # Should have parameters for file path and options
        assert len(sig.parameters) > 0


class TestPrintResults:
    """Test the print_results function."""

    def test_print_results_exists(self):
        """Test that print_results function exists."""
        assert callable(print_results)

    def test_print_results_with_empty_list(self, capsys):
        """Test printing results with empty list."""
        print_results([])
        captured = capsys.readouterr()
        # Should handle empty list gracefully
        assert captured.out is not None

    def test_print_results_with_single_result(self, capsys):
        """Test printing results with a single result."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=1.0,
            decompress_time=0.5,
            input_size=1000000,
            compressed_size=500000,
        )

        print_results([result])
        captured = capsys.readouterr()

        # Should contain algo name
        assert "zlib" in captured.out.lower()

    def test_print_results_with_multiple_results(self, capsys):
        """Test printing results with multiple results."""
        results = [
            BenchmarkResult(
                algo="zlib",
                strategy="balanced",
                level=6,
                compress_time=1.0,
                decompress_time=0.5,
                input_size=1000000,
                compressed_size=500000,
            ),
            BenchmarkResult(
                algo="zstd",
                strategy="fast",
                level=1,
                compress_time=0.5,
                decompress_time=0.3,
                input_size=1000000,
                compressed_size=450000,
            ),
        ]

        print_results(results)
        captured = capsys.readouterr()

        # Should contain both algo names
        assert "zlib" in captured.out.lower()
        assert "zstd" in captured.out.lower()

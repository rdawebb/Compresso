"""Tests for the benchmark module."""

import pytest

from compresso.introspect.benchmark import (
    BenchmarkResult,
    benchmark_file,
)

MIB = 1024 * 1024


class TestBenchmarkResult:
    """Test the ratio and speeds a BenchmarkResult derives from its timings."""

    @pytest.mark.parametrize(
        "compress_time,decompress_time,input_size,compressed_size,expected",
        [
            (1.0, 0.5, 2 * MIB, MIB, (0.5, 2.0, 4.0)),
            # Nothing read: no ratio or speed to report, rather than dividing by 0
            (1.0, 0.5, 0, 100, (0.0, 0.0, 0.0)),
            # Too quick to time: the speeds report 0 rather than dividing by 0
            (0.0, 0.0, MIB, MIB // 2, (0.5, 0.0, 0.0)),
        ],
        ids=["normal", "empty-input", "untimed"],
    )
    def test_derived_figures(
        self,
        compress_time: float,
        decompress_time: float,
        input_size: int,
        compressed_size: int,
        expected: tuple[float, float, float],
    ) -> None:
        """Test ratio, comp_mb_s and decomp_mb_s, including their zero guards."""
        result = BenchmarkResult(
            algo="zlib",
            strategy="balanced",
            level=6,
            compress_time=compress_time,
            decompress_time=decompress_time,
            input_size=input_size,
            compressed_size=compressed_size,
        )

        assert (result.ratio, result.comp_mb_s, result.decomp_mb_s) == pytest.approx(
            expected
        )


class TestBenchmarkFile:
    """Test the benchmark_file function."""

    def test_levels_outside_a_backends_range_are_skipped(
        self, sample_text_file
    ) -> None:
        """Test that a shared level grid skips what each backend rejects."""
        results = benchmark_file(
            sample_text_file,
            algos=["zlib", "zstd", "snappy"],
            strategies=["balanced"],
            levels=[None, 15],
            update_cache=False,
        )

        ran = {(r.algo, r.level) for r in results}
        assert ran == {("zlib", None), ("zstd", None), ("zstd", 15), ("snappy", None)}

"""Tests for the speeds module."""

import json
from pathlib import Path

import pytest

from compresso.introspect import speeds
from compresso.introspect.benchmark import BenchmarkResult
from compresso.introspect.capabilities import list_capabilities
from compresso.introspect.speeds import get_estimated_speeds, update_from_benchmarks

MIB = 1024 * 1024


def write_speeds(path: Path, data: object) -> None:
    """Write a speeds file as a previous benchmark run would have.

    Args:
        path: The speeds file to write.
        data: The JSON document to store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def timed_result(algo: str, compress_time: float, decompress_time: float) -> object:
    """A benchmark result over 1 MiB, so each speed is 1 / its time.

    Args:
        algo: The backend benchmarked.
        compress_time: Seconds taken to compress.
        decompress_time: Seconds taken to decompress.

    Returns:
        The benchmark result.
    """
    return BenchmarkResult(
        algo=algo,
        strategy="balanced",
        level=None,
        compress_time=compress_time,
        decompress_time=decompress_time,
        input_size=MIB,
        compressed_size=MIB // 2,
    )


class TestGetEstimatedSpeeds:
    """Test the get_estimated_speeds function."""

    def test_every_backend_has_a_default(self) -> None:
        """Test that no compiled backend falls through to the generic guess."""
        names = {cap.name for cap in list_capabilities()}

        assert names <= speeds._DEFAULT_COMP_MB_S.keys()
        assert names <= speeds._DEFAULT_DECOMP_MB_S.keys()

    @pytest.mark.parametrize("operation", ["compress", "decompress"])
    def test_falls_back_to_the_defaults_without_a_file(self, operation: str) -> None:
        """Test that with nothing measured, the built-in estimate is used."""
        defaults = (
            speeds._DEFAULT_COMP_MB_S
            if operation == "compress"
            else speeds._DEFAULT_DECOMP_MB_S
        )

        assert get_estimated_speeds("zstd", operation=operation) == defaults["zstd"]

    def test_default_operation_is_decompress(self) -> None:
        """Test that omitting the operation asks for the decompression speed."""
        assert get_estimated_speeds("zlib") == get_estimated_speeds(
            "zlib", operation="decompress"
        )

    def test_measured_speeds_replace_the_defaults(self, speeds_file: Path) -> None:
        """Test that a benchmarked speed wins, however the name is cased."""
        write_speeds(speeds_file, {"zlib": {"comp_mb_s": 123.0, "decomp_mb_s": 456.0}})

        assert get_estimated_speeds("ZLIB", operation="compress") == 123.0
        assert get_estimated_speeds("zlib", operation="decompress") == 456.0

    def test_unmeasured_operation_falls_back(self, speeds_file: Path) -> None:
        """Test that a zero measurement is treated as missing, not as infinitely slow."""
        write_speeds(speeds_file, {"zlib": {"comp_mb_s": 0.0, "decomp_mb_s": 456.0}})

        assert (
            get_estimated_speeds("zlib", operation="compress")
            == speeds._DEFAULT_COMP_MB_S["zlib"]
        )

    def test_corrupt_file_falls_back(self, speeds_file: Path) -> None:
        """Test that an unreadable speeds file is ignored rather than raising."""
        speeds_file.parent.mkdir(parents=True)
        speeds_file.write_text("{not json", encoding="utf-8")

        assert get_estimated_speeds("zlib") == speeds._DEFAULT_DECOMP_MB_S["zlib"]


class TestUpdateFromBenchmarks:
    """Test the update_from_benchmarks function."""

    @pytest.mark.parametrize(
        "results",
        [[], [timed_result("zlib", 0.0, 0.0)]],
        ids=["no-results", "untimed-result"],
    )
    def test_nothing_measurable_writes_nothing(
        self, speeds_file: Path, results: list[object]
    ) -> None:
        """Test that a run with no usable timing leaves no speeds file behind."""
        update_from_benchmarks(results)

        assert not speeds_file.exists()

    def test_records_the_average_of_a_run(self, speeds_file: Path) -> None:
        """Test that each backend's speeds are averaged across its results."""
        update_from_benchmarks(
            [timed_result("zlib", 1.0, 0.5), timed_result("zlib", 0.5, 0.25)]
        )

        assert json.loads(speeds_file.read_text(encoding="utf-8")) == {
            "zlib": {"comp_mb_s": 1.5, "decomp_mb_s": 3.0, "samples": 2}
        }

    def test_merges_with_earlier_samples(self, speeds_file: Path) -> None:
        """Test that a new run is weighted against what was already recorded."""
        write_speeds(
            speeds_file,
            {"zlib": {"comp_mb_s": 3.0, "decomp_mb_s": 4.0, "samples": 1}},
        )

        update_from_benchmarks([timed_result("zlib", 1.0, 0.5)])

        assert json.loads(speeds_file.read_text(encoding="utf-8")) == {
            "zlib": {"comp_mb_s": 2.0, "decomp_mb_s": 3.0, "samples": 2}
        }

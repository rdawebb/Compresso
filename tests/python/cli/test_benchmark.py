"""Tests for `compresso benchmark`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import compresso.cli.benchmark as benchmark_cmd
from compresso.cli._render import EXIT_USAGE
from compresso.introspect.benchmark import BenchmarkResult

from .conftest import invoke


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record each `benchmark_file` call, answering with one canned result."""
    seen: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> list[BenchmarkResult]:
        seen.append(kwargs)
        return [BenchmarkResult("zstd", "fast", None, 0.5, 0.25, 1000, 400)]

    monkeypatch.setattr(benchmark_cmd, "benchmark_file", record)
    return seen


class TestBenchmark:
    """Test how `benchmark` turns its flags into a run, and prints it."""

    @pytest.mark.parametrize(
        "args,algos,strategies,levels,repeats",
        [
            ((), None, None, [None], 1),
            (
                (
                    "-a",
                    "zstd",
                    "--algo",
                    "lz4",
                    "-s",
                    "fast",
                    "--strategy",
                    "max_ratio",
                ),
                ["zstd", "lz4"],
                ["fast", "max_ratio"],
                [None],
                1,
            ),
            (("-l", "3", "--level", "AUTO", "-l", "9"), None, None, [3, None, 9], 1),
            (("-r", "3"), None, None, [None], 3),
        ],
        ids=["defaults", "algo-strategy", "level", "repeats"],
    )
    def test_flags_reach_benchmark_file(
        self,
        calls: list[dict[str, Any]],
        payload: Path,
        args: tuple[str, ...],
        algos: list[str] | None,
        strategies: list[str] | None,
        levels: list[int | None],
        repeats: int,
    ) -> None:
        """Test that each repeated flag arrives as a list, and an omitted -a or -s
        as None, meaning all."""
        invoke("benchmark", payload, *args)

        (call,) = calls
        assert call["algos"] == algos
        assert call["strategies"] == strategies
        assert call["levels"] == levels
        assert call["repeats"] == repeats

    def test_prints_settings_then_table(
        self, calls: list[dict[str, Any]], payload: Path
    ) -> None:
        """Test the header lines and that the table names the auto level."""
        output = invoke(
            "benchmark", payload, "-a", "zstd", "-l", "auto", "-l", "3"
        ).output

        assert output.startswith(
            f"Running benchmarks on: {payload}\nRepeats: 1\n"
            "Algorithms: zstd\nLevels: auto, 3\n\n"
        )
        assert "| zstd   | fast       | auto    |" in output

    @pytest.mark.parametrize("level", ["nope", "1.5", ""])
    def test_bad_level_is_usage(
        self, calls: list[dict[str, Any]], payload: Path, level: str
    ) -> None:
        """Test that a level that is neither a number nor auto is a usage error."""
        invoke("benchmark", payload, "-l", level, expect=EXIT_USAGE)

        assert not calls

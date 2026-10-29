"""What `benchmark` prints: the run's settings, then a table of results."""

from __future__ import annotations

from pathlib import Path

from ...introspect.benchmark import BenchmarkResult


def _level_name(level: int | None) -> str | int:
    return "auto" if level is None else level


def benchmark_header(
    file: Path,
    repeats: int,
    algos: list[str] | None,
    strategies: list[str] | None,
    levels: list[int | None],
) -> None:
    """Print what the run covers, then a blank line.

    Args:
        file: The file being benchmarked.
        repeats: How many times each combination runs.
        algos: The algorithms asked for, or None for all.
        strategies: The strategies asked for, or None for all.
        levels: The levels asked for, None meaning the strategy's own.
    """
    lines: list[str] = [f"Running benchmarks on: {file}", f"Repeats: {repeats}"]
    if algos:
        lines.append(f"Algorithms: {', '.join(algos)}")

    if strategies:
        lines.append(f"Strategies: {', '.join(strategies)}")

    lines.append(f"Levels: {', '.join(str(_level_name(lv)) for lv in levels)}")
    print("\n".join(lines) + "\n")


def benchmark_results(results: list[BenchmarkResult]) -> None:
    """Print one table row per algorithm, strategy and level.

    Args:
        results: The benchmark's results, in run order.
    """
    headers: list[str] = [
        "Algo",
        "Strategy",
        "Level",
        "Comp Time (s)",
        "Decomp Time (s)",
        "MB/s (Comp)",
        "MB/s (Decomp)",
        "Ratio (comp/orig)",
    ]

    table_data: list[list[str | int]] = [
        [
            r.algo,
            r.strategy,
            _level_name(r.level),
            f"{r.compress_time:.4f}",
            f"{r.decompress_time:.4f}",
            f"{r.comp_mb_s:.2f}",
            f"{r.decomp_mb_s:.2f}",
            f"{r.ratio:.3f}",
        ]
        for r in results
    ]

    # Deferred: tabulate pulls in wcwidth, which every CLI command would
    # otherwise pay for
    from tabulate import tabulate

    print(tabulate(tabular_data=table_data, headers=headers, tablefmt="grid"))

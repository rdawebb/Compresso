"""The `benchmark` command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from ..introspect.benchmark import benchmark_file
from ._app import app
from ._render import EXIT_USAGE, cancelled, fail, succeed
from ._render.benchmark import benchmark_header, benchmark_results


def _parse_level(value: str) -> int | None:
    """Parse one `-l` value, exiting 2 if it's neither a number nor `auto`.

    Args:
        value: The text given.

    Returns:
        The level, or None for `auto`, the strategy's own.
    """
    if value.lower() == "auto":
        return None

    try:
        return int(value)

    except ValueError:
        fail(f"Error: Invalid level {value!r}; use an integer or 'auto'", EXIT_USAGE)


def benchmark(
    file: Annotated[Path, app.Argument(help="File to benchmark")],
    algo: Annotated[
        list[str] | None,
        app.Option("--algo", "-a", help="Algorithm to run; repeatable (default: all)"),
    ] = None,
    strategy: Annotated[
        list[str] | None,
        app.Option(
            "--strategy", "-s", help="Strategy to run; repeatable (default: all)"
        ),
    ] = None,
    level: Annotated[
        list[str] | None,
        app.Option(
            "--level",
            "-l",
            metavar="N|auto",
            help="Level to run, or 'auto'; repeatable; a level outside an "
            "algorithm's range is skipped for that algorithm",
            show_default="auto",
        ),
    ] = None,
    repeats: Annotated[
        int,
        app.Option(
            "--repeats", "-r", help="Number of times to repeat each benchmark", min=1
        ),
    ] = 1,
    temp_dir: Annotated[
        Path | None,
        app.Option("--temp-dir", help="Temporary directory for benchmark files"),
    ] = None,
    update_cache: Annotated[
        bool,
        app.Option(
            "--update-cache",
            help="Update speed estimates cache with benchmark results",
        ),
    ] = False,
) -> None:
    """Run compression benchmarks on a file.
    \f

    Args:
        file: The path to the file to benchmark.
        algo: Algorithms to run (default: all available).
        strategy: Strategies to run (default: all).
        level: Levels to run, or `auto` for the strategy's own (default: auto).
        repeats: Number of times to repeat each benchmark (default: 1).
        temp_dir: Temporary directory for benchmark files (default: None).
        update_cache: If True, update the speed estimates cache with benchmark results (default: False).
    """
    levels: list[int | None] = [_parse_level(v) for v in level or ["auto"]]

    try:
        benchmark_header(file, repeats, algo, strategy, levels)

        results = benchmark_file(
            src=file,
            algos=algo,
            strategies=strategy,
            levels=levels,
            repeats=repeats,
            temp_dir=temp_dir,
            update_cache=update_cache,
        )

        if not results:
            fail("No benchmark results generated\n")

        benchmark_results(results)

        if update_cache:
            print()
            succeed("Speed cache updated\n")
            print()

    except KeyboardInterrupt:
        cancelled("Benchmark")

    except FileNotFoundError as e:
        fail(f"File not found: {e}\n", EXIT_USAGE)

    except Exception as e:
        fail(f"Benchmark error: {e}\n")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["b"])(benchmark)

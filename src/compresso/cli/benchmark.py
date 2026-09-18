"""The `benchmark` command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from ..backend.benchmark import benchmark_file, print_results
from ._app import app
from ._render import EXIT_USAGE, cancelled, fail, succeed


def benchmark(
    file: Annotated[Path, app.Argument(help="File to benchmark")],
    algos: Annotated[
        str | None, app.Option("--algos", help="Comma-separated list of algorithms")
    ] = "all",
    strategies: Annotated[
        str | None,
        app.Option("--strategies", help="Comma-separated list of strategies"),
    ] = "all",
    levels: Annotated[
        str | None, app.Option("--levels", help="Comma-separated list of levels (0-9)")
    ] = "auto",
    repeats: Annotated[
        int, app.Option("--repeats", help="Number of times to repeat each benchmark")
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
        algos: Comma-separated list of algorithms to use (default: all available).
        strategies: Comma-separated list of strategies to use (default: all).
        levels: Comma-separated list of levels to use (default: all).
        repeats: Number of times to repeat each benchmark (default: 1).
        temp_dir: Temporary directory for benchmark files (default: None).
        update_cache: If True, update the speed estimates cache with benchmark results (default: False).
    """
    try:
        algo_list: list[str] = []
        if algos and algos.lower() != "all":
            algo_list = [a.strip() for a in algos.split(",") if a.strip()]

        strategy_list: list[str] = []
        if strategies and strategies.lower() != "all":
            strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]

        level_list: list[int | None] = []
        if levels:
            for level in levels.split(sep=","):
                level: str = level.strip()
                if not level:
                    continue

                if level.lower() in ("auto", "default"):
                    level_list.append(None)

                else:
                    try:
                        level_list.append(int(level))

                    except ValueError:
                        fail(
                            f"Invalid level: {level}. Use integers 0-9 or 'auto'",
                            EXIT_USAGE,
                        )

        app.echo(message=f"Running benchmarks on: {file}")
        app.echo(message=f"Repeats: {repeats}")
        if algo_list:
            app.echo(message=f"Algorithms: {', '.join(algo_list)}")

        if strategy_list:
            app.echo(message=f"Strategies: {', '.join(strategy_list)}")

        if level_list is not None:
            level_str: str = ", ".join(
                str(object=level) if level is not None else "auto"
                for level in level_list
            )
            app.echo(message=f"Levels: {level_str}")
        app.echo()

        results = benchmark_file(
            src=file,
            algos=algo_list or None,
            strategies=strategy_list or None,
            levels=level_list or None,
            repeats=repeats,
            temp_dir=temp_dir,
            update_cache=update_cache,
        )

        if not results:
            fail("No benchmark results generated\n")

        print_results(results)

        if update_cache:
            app.echo()
            succeed("Speed cache updated\n")
            app.echo()

    except KeyboardInterrupt:
        cancelled("Benchmark")

    except FileNotFoundError as e:
        fail(f"File not found: {e}\n", EXIT_USAGE)

    except Exception as e:
        fail(f"Benchmark error: {e}\n")


def register() -> None:
    """Attach this command to the app."""
    app.command(aliases=["b", "bench"])(benchmark)

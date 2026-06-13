"""Main CLI for Compresso compression and decompression tool."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Union

import click
from typer_extensions import ExtendedTyper

from ._core import (
    BackendError,
    Error,
    HeaderError,
)
from .backend.benchmark import BenchmarkResult, benchmark_file, print_results
from .backend.capabilities import BackendCapabilities, list_capabilities
from .backend.file_inspect import InspectResult
from .backend.file_inspect import inspect as inspect_file
from .frontend.api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
    JobResult,
)

app = ExtendedTyper(help="Compresso - Fast file compression and decompression tool")


def format_size(size_bytes: float) -> str:
    """Format byte size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"

        size_bytes: float = size_bytes / 1024.0

    return f"{size_bytes:.2f} PB"


def format_time(seconds: float) -> str:
    """Format time duration in human-readable format.

    Args:
        seconds: Time in seconds

    Returns:
        Formatted string (e.g., "1.5s", "2m 30s")
    """
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"

    elif seconds < 60:
        return f"{seconds:.2f}s"

    else:
        mins: int = int(seconds // 60)
        secs: float = seconds % 60

        return f"{mins}m {secs:.1f}s"


@app.command_with_aliases(aliases=["c", "comp"])
def compress(
    file: Path = app.Argument(default=..., help="File to compress"),
    output: Union[Path, None] = app.Option(
        None, "--output", "-o", help="Output file path (default: input.comp)"
    ),
    algo: Union[str, None] = app.Option(
        "auto",
        "--algo",
        "-a",
        case_sensitive=False,
        help="Compression algorithm to use",
    ),
    strategy: str = app.Option(
        "balanced",
        "--strategy",
        "-s",
        case_sensitive=False,
        help="Compression strategy to use (fast/balanced/max_ratio)",
    ),
    level: Union[int, None] = app.Option(
        None, "--level", "-l", min=0, max=9, help="Compression level (0-9)"
    ),
    quiet: bool = app.Option(False, "--quiet", "-q", help="Suppress all output"),
):
    """Compress a file using the specified algorithm and strategy.

    Args:
        file: The path to the file to compress.
        output: The path to the output file (default: None).
        algo: The compression algorithm to use (default: None).
        strategy: The compression strategy to use (default: "balanced").
        level: The compression level to use (default: None).
        quiet: If True, suppress all output (default: False).
    """
    try:
        algo_lower: str | None = algo.lower() if algo else None
        options = CompressionOptions(
            algo=None if algo_lower == "auto" else algo_lower,
            strategy=strategy.lower(),
            level=level,
        )

        job: CompressionJob = CompressionJob.from_file(
            src=file, dest=output, options=options
        )
        plan: CompressionPlan = job.plan

        if not plan.can_compress:
            app.echo(
                message=app.style(
                    text=f"✗ Error: {plan.reason_if_unavailable}", fg="red"
                ),
                err=True,
            )
            sys.exit(1)

        if not quiet:
            app.echo(message=f"Compressing: {plan.src}")
            app.echo(message=f"Output:      {plan.dest}")
            app.echo(message=f"Algorithm:   {plan.backend_name}")
            app.echo(message=f"Strategy:    {strategy}")
            if level is not None:
                app.echo(message=f"Level:       {level}")
            app.echo()

        start_time: float = time.time()

        if not quiet and plan.input_size > 1024 * 1024:
            with click.progressbar(
                length=plan.input_size,
                label="Compressing",
                show_eta=True,
                show_percent=True,
            ) as bar:

                def progress_callback(fraction, current, total):
                    bar.update(n_steps=current - bar.pos)

                result: JobResult = job.run(progress=progress_callback)

        else:
            result: JobResult = job.run()

        elapsed: float = time.time() - start_time

        if not result.ok:
            app.echo(
                message=app.style(
                    text=f"\n✗ Compression failed: {result.error}", fg="red"
                ),
                err=True,
            )
            sys.exit(1)

        compressed_size: int = plan.dest.stat().st_size
        ratio: int | float = (
            (compressed_size / plan.input_size) * 100 if plan.input_size > 0 else 0
        )
        speed_mbs: int | float = (
            (plan.input_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        )

        if not quiet:
            app.echo()
            app.echo(message=app.style(text="✓ Compression successful!", fg="green"))
            app.echo(
                message=f"  Original size:   {format_size(size_bytes=plan.input_size)}"
            )
            app.echo(
                message=f"  Compressed size: {format_size(size_bytes=compressed_size)}"
            )
            app.echo(message=f"  Ratio:           {ratio:.1f}% of original")
            app.echo(message=f"  Time:            {format_time(seconds=elapsed)}")
            app.echo(message=f"  Speed:           {speed_mbs:.2f} MB/s")
            app.echo(
                message=f"  Saved:           {format_size(size_bytes=plan.input_size - compressed_size)}"
            )
            app.echo()

    except (Error, HeaderError, BackendError) as e:
        app.echo(
            message=app.style(text=f"✗ Compression error: {e}", fg="red"), err=True
        )
        sys.exit(1)

    except Exception as e:
        app.echo(message=app.style(text=f"✗ Unexpected error: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["d", "decomp"])
def decompress(
    file: Path = app.Argument(..., help="File to decompress"),
    output: Union[Path, None] = app.Option(
        None,
        "--output",
        "-o",
        help="Output file path (default: remove .comp extension)",
    ),
    quiet: bool = app.Option(False, "--quiet", "-q", help="Suppress progress output"),
):
    """Decompress a Compresso compressed file.

    Args:
        file: The path to the compressed file.
        output: The path to the output file (default: remove .comp extension).
        quiet: If True, suppress progress output.
    """
    try:
        job: DecompressionJob = DecompressionJob.from_file(src=file, dest=output)
        plan: DecompressionPlan = job.plan
        insp: InspectResult = plan.inspection

        if not insp.is_compresso:
            app.echo(
                message=app.style(
                    text=f"✗ Error: Not a valid Compresso file\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not insp.header_ok:
            app.echo(
                message=app.style(
                    text=f"✗ Error: Invalid file header\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not insp.can_decompress:
            app.echo(
                message=app.style(
                    text=f"✗ Error: Cannot decompress file\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not quiet:
            app.echo(message=f"Decompressing: {plan.src}")
            app.echo(message=f"Output:        {plan.dest}")
            app.echo(message=f"Algorithm:     {insp.algo_name}")
            if insp.orig_size:
                app.echo(
                    message=f"Original size: {format_size(size_bytes=insp.orig_size)}"
                )
            app.echo()

        start_time: float = time.time()

        if not quiet and insp.orig_size and insp.orig_size > 1024 * 1024:
            with click.progressbar(
                length=insp.orig_size,
                label="Decompressing",
                show_eta=True,
                show_percent=True,
            ) as bar:

                def progress_callback(fraction, current, total):
                    bar.update(n_steps=current - bar.pos)

                result: JobResult = job.run(progress=progress_callback)

        else:
            result: JobResult = job.run()

        elapsed: float = time.time() - start_time

        if not result.ok:
            app.echo(
                message=app.style(
                    text=f"\n✗ Decompression failed: {result.error}", fg="red"
                ),
                err=True,
            )
            sys.exit(1)

        decompressed_size: int = plan.dest.stat().st_size
        compressed_size: int = plan.src.stat().st_size
        speed_mbs: int | float = (
            (decompressed_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        )

        if not quiet:
            app.echo()
            app.echo(message=app.style(text="✓ Decompression successful!", fg="green"))
            app.echo(
                message=f"  Compressed size:   {format_size(size_bytes=compressed_size)}"
            )
            app.echo(
                message=f"  Decompressed size: {format_size(size_bytes=decompressed_size)}"
            )
            app.echo(message=f"  Time:              {format_time(seconds=elapsed)}")
            app.echo(message=f"  Speed:             {speed_mbs:.2f} MB/s")
            app.echo()

    except (Error, HeaderError, BackendError) as e:
        app.echo(
            message=app.style(text=f"✗ Decompression error: {e}", fg="red"), err=True
        )
        sys.exit(1)

    except Exception as e:
        app.echo(message=app.style(text=f"✗ Unexpected error: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["i", "info"])
def inspect(
    file: Path = app.Argument(..., help="File to inspect"),
    output_json: bool = app.Option(False, "--json", help="Output in JSON format"),
):
    """Inspect a compressed file and show metadata.

    Args:
        file: The path to the compressed file.
        output_json: If True, output metadata in JSON format.
    """
    try:
        result: InspectResult = inspect_file(path=file)

        if output_json:
            import json

            data: dict[str, str | int | None | float] = {
                "path": str(object=result.path),
                "is_compresso": result.is_compresso,
                "header_ok": result.header_ok,
                "version": result.version,
                "algo_id": result.algo_id,
                "algo_name": result.algo_name,
                "level": result.level,
                "flags": result.flags,
                "orig_size": result.orig_size,
                "backend_available": result.backend_available,
                "has_streaming": result.has_streaming,
                "can_decompress": result.can_decompress,
                "estimated_decomp_s": result.estimated_decomp_s,
                "reason": result.reason,
            }
            app.echo(message=json.dumps(obj=data, indent=2))
            return

        app.echo(message=f"File: {result.path}")
        app.echo()

        if not result.is_compresso:
            app.echo(message=app.style(text="✗ Not a valid Compresso file", fg="red"))
            if result.reason:
                app.echo(message=f"  Reason: {result.reason}")
            sys.exit(1)

        if not result.header_ok:
            app.echo(message=app.style(text="✗ Invalid file header", fg="yellow"))
            if result.reason:
                app.echo(message=f"  Reason: {result.reason}")
            sys.exit(1)

        app.echo(message=app.style(text="✓ Valid Compresso file", fg="green"))
        app.echo()
        app.echo(
            message=f"Algorithm:       {result.algo_name or 'Unknown'} (ID: {result.algo_id})"
        )
        app.echo(message=f"Version:         {result.version}")
        app.echo()

        if result.level is not None:
            app.echo(message=f"Level:           {result.level}")

        else:
            app.echo(message="Level:           auto")

        if result.orig_size:
            app.echo(
                message=f"Original size:   {format_size(size_bytes=result.orig_size)}"
            )

        compressed_size: int = file.stat().st_size
        app.echo(message=f"Compressed size: {format_size(size_bytes=compressed_size)}")

        if result.orig_size:
            ratio: int | float = (compressed_size / result.orig_size) * 100
            app.echo(message=f"Compression:     {ratio:.1f}% of original")

        app.echo()
        app.echo(
            message=f"Backend available:  {'Yes' if result.backend_available else 'No'}"
        )
        app.echo(
            message=f"Streaming support:  {'Yes' if result.has_streaming else 'No'}"
        )
        app.echo(
            message=f"Can decompress:     {'Yes' if result.can_decompress else 'No'}"
        )

        if result.estimated_decomp_s:
            app.echo(
                message=f"Est. decomp time:   {format_time(seconds=result.estimated_decomp_s)}\n"
            )

        if not result.can_decompress and result.reason:
            app.echo()
            app.echo(message=app.style(text=f"⚠ {result.reason}", fg="yellow"))

    except Exception as e:
        app.echo(
            message=app.style(text=f"✗ Error inspecting file: {e}", fg="red"), err=True
        )
        sys.exit(1)


@app.command_with_aliases(aliases=["b", "bench"])
def benchmark(
    file: Path = app.Argument(..., help="File to benchmark"),
    algos: Union[str, None] = app.Option(
        "all", "--algos", help="Comma-separated list of algorithms"
    ),
    strategies: Union[str, None] = app.Option(
        "all", "--strategies", help="Comma-separated list of strategies"
    ),
    levels: Union[str, None] = app.Option(
        "auto", "--levels", help="Comma-separated list of levels (0-9)"
    ),
    repeats: int = app.Option(
        1, "--repeats", help="Number of times to repeat each benchmark"
    ),
    temp_dir: Union[Path, None] = app.Option(
        None, "--temp-dir", help="Temporary directory for benchmark files"
    ),
    update_cache: bool = app.Option(
        False,
        "--update-cache",
        help="Update speed estimates cache with benchmark results",
    ),
):
    """Run compression benchmarks on a file.

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
        algo_list: List[str] = []
        if algos and algos.lower() != "all":
            algo_list = [a.strip() for a in algos.split(",") if a.strip()]

        strategy_list: List[str] = []
        if strategies and strategies.lower() != "all":
            strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]

        level_list: List[Union[int, None]] = []
        if levels:
            level_list: List[Union[int, None]] = []
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
                        app.echo(
                            message=app.style(
                                text=f"✗ Invalid level: {level}. Use integers 0-9 or 'auto'",
                                fg="red",
                            ),
                            err=True,
                        )
                        sys.exit(1)

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

        results: List[BenchmarkResult] = benchmark_file(
            src=file,
            algos=algo_list or None,
            strategies=strategy_list or None,
            levels=level_list or None,
            repeats=repeats,
            temp_dir=temp_dir,
            update_cache=update_cache,
        )

        if not results:
            app.echo(
                message=app.style(text="✗ No benchmark results generated\n", fg="red"),
                err=True,
            )
            sys.exit(1)

        print_results(results)

        if update_cache:
            app.echo()
            app.echo(message=app.style(text="✓ Speed cache updated\n", fg="green"))
            app.echo()

    except FileNotFoundError as e:
        app.echo(message=app.style(text=f"✗ File not found: {e}\n", fg="red"), err=True)
        sys.exit(1)

    except Exception as e:
        app.echo(
            message=app.style(text=f"✗ Benchmark error: {e}\n", fg="red"), err=True
        )
        sys.exit(1)


@app.command_with_aliases(aliases=["l", "ls"])
def list():
    """List all available compression algorithms."""
    try:
        caps: List[BackendCapabilities] = list_capabilities()

        if not caps:
            app.echo(
                message=app.style(text="✗ No compression backends available", fg="red")
            )
            sys.exit(1)

        app.echo(message=f"Available compression algorithms: {len(caps)}")
        app.echo()

        for cap in caps:
            app.echo(message=app.style(text=f"● {cap.name}", fg="green", bold=True))
            app.echo(message=app.style(text=f"  ID:              {cap.id}"))
            app.echo(
                message=app.style(
                    text=f"  Buffer mode:     {'Yes' if cap.has_buffer else 'No'}"
                )
            )
            app.echo(
                message=app.style(
                    text=f"  Streaming mode:  {'Yes' if cap.has_stream else 'No'}"
                )
            )
            app.echo()

    except Exception as e:
        app.echo(
            message=app.style(text=f"✗ Error listing algorithms: {e}", fg="red"),
            err=True,
        )
        sys.exit(1)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

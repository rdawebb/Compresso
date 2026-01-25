"""Main CLI for Compresso compression and decompression tool."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click
from typer_extensions import ExtendedTyper

from src.compresso import (
    BackendError,
    Error,
    HeaderError,
)
from src.compresso.backend.benchmark import benchmark_file, print_results
from src.compresso.backend.capabilities import list_capabilities
from src.compresso.backend.file_inspect import inspect as inspect_file
from src.compresso.frontend.api import (
    CompressionJob,
    CompressionOptions,
    DecompressionJob,
)

app = ExtendedTyper(help="Compresso - Fast file compression and decompression tool")


def format_size(size_bytes: int) -> str:
    """Format byte size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes = int(size_bytes / 1024.0)
    return f"{size_bytes:.2f} PB"


def format_time(seconds: float) -> str:
    """Format time duration in human-readable format.
    
    Args:
        seconds: Time in seconds
        
    Returns:
        Formatted string (e.g., "1.5s", "2m 30s")
    """
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"


@app.command_with_aliases(aliases=["c", "comp"])
def compress(
    file: Path = app.Argument(..., help="File to compress"),
    output: Path | None = app.Option(None, "--output", "-o", help="Output file path (default: input.comp)"),
    algo: str | None = app.Option("auto", "--algo", "-a", case_sensitive=False, help="Compression algorithm to use"),
    strategy: str = app.Option("balanced", "--strategy", "-s", case_sensitive=False, help="Compression strategy to use (fast/balanced/max_ratio)"),
    level: int | None = app.Option(None, "--level", "-l", min=0, max=9, help="Compression level (0-9)"),
    quiet: bool = app.Option(False, "--quiet", "-q", help="Suppress all output"),
):
    """Compress a file using the specified algorithm and strategy.

    Args:
        file (Path): The path to the file to compress.
        output (Path | None): The path to the output file (default: None).
        algo (str | None): The compression algorithm to use (default: None).
        strategy (str): The compression strategy to use (default: "balanced").
        level (int | None): The compression level to use (default: None).
        quiet (bool): If True, suppress all output (default: False).
    """
    try:
        algo_lower = algo.lower() if algo else None
        options = CompressionOptions(
            algo=None if algo_lower == "auto" else algo_lower,
            strategy=strategy.lower(),
            level=level,
        )

        job = CompressionJob.from_file(file, output, options)
        plan = job.plan

        if not plan.can_compress:
            app.echo(
                app.style(f"✗ Error: {plan.reason_if_unavailable}", fg="red"),
                err=True,
            )
            sys.exit(1)

        if not quiet:
            app.echo(f"Compressing: {plan.src}")
            app.echo(f"Output:      {plan.dest}")
            app.echo(f"Algorithm:   {plan.backend_name}")
            app.echo(f"Strategy:    {strategy}")
            if level is not None:
                app.echo(f"Level:       {level}")
            app.echo()

        start_time = time.time()

        if not quiet and plan.input_size > 1024 * 1024:
            with click.progressbar(
                length=plan.input_size,
                label="Compressing",
                show_eta=True,
                show_percent=True,
            ) as bar:

                def progress_callback(fraction, current, total):
                    bar.update(current - bar.pos)

                result = job.run(progress=progress_callback)
        else:
            result = job.run()

        elapsed = time.time() - start_time

        if not result.ok:
            app.echo(app.style(f"\n✗ Compression failed: {result.error}", fg="red"), err=True)
            sys.exit(1)

        compressed_size = plan.dest.stat().st_size
        ratio = (compressed_size / plan.input_size) * 100 if plan.input_size > 0 else 0
        speed_mbs = (plan.input_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        if not quiet:
            app.echo()
            app.echo(app.style("✓ Compression successful!", fg="green"))
            app.echo(f"  Original size:   {format_size(plan.input_size)}")
            app.echo(f"  Compressed size: {format_size(compressed_size)}")
            app.echo(f"  Ratio:           {ratio:.1f}% of original")
            app.echo(f"  Time:            {format_time(elapsed)}")
            app.echo(f"  Speed:           {speed_mbs:.2f} MB/s")
            app.echo(f"  Saved:           {format_size(plan.input_size - compressed_size)}")
            app.echo()

    except (Error, HeaderError, BackendError) as e:
        app.echo(app.style(f"✗ Compression error: {e}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        app.echo(app.style(f"✗ Unexpected error: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["d", "decomp"])
def decompress(
    file: Path = app.Argument(..., help="File to decompress"),
    output: Path | None = app.Option(None, "--output", "-o", help="Output file path (default: remove .comp extension)"),
    quiet: bool = app.Option(False, "--quiet", "-q", help="Suppress progress output"),
):
    """Decompress a Compresso compressed file.

    Args:
        file (Path): The path to the compressed file.
        output (Path | None): The path to the output file (default: remove .comp extension).
        quiet (bool): If True, suppress progress output.
    """
    try:
        job = DecompressionJob.from_file(file, output)
        plan = job.plan
        insp = plan.inspection

        if not insp.is_compresso:
            app.echo(
                app.style(
                    f"✗ Error: Not a valid Compresso file\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not insp.header_ok:
            app.echo(
                app.style(
                    f"✗ Error: Invalid file header\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not insp.can_decompress:
            app.echo(
                app.style(
                    f"✗ Error: Cannot decompress file\n  Reason: {insp.reason}",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)

        if not quiet:
            app.echo(f"Decompressing: {plan.src}")
            app.echo(f"Output:        {plan.dest}")
            app.echo(f"Algorithm:     {insp.algo_name}")
            if insp.orig_size:
                app.echo(f"Original size: {format_size(insp.orig_size)}")
            app.echo()

        start_time = time.time()

        if not quiet and insp.orig_size and insp.orig_size > 1024 * 1024:
            with click.progressbar(
                length=insp.orig_size,
                label="Decompressing",
                show_eta=True,
                show_percent=True,
            ) as bar:

                def progress_callback(fraction, current, total):
                    bar.update(current - bar.pos)

                result = job.run(progress=progress_callback)
        else:
            result = job.run()

        elapsed = time.time() - start_time

        if not result.ok:
            app.echo(
                app.style(f"\n✗ Decompression failed: {result.error}", fg="red"),
                err=True,
            )
            sys.exit(1)

        decompressed_size = plan.dest.stat().st_size
        compressed_size = plan.src.stat().st_size
        speed_mbs = (
            (decompressed_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        )

        if not quiet:
            app.echo()
            app.echo(app.style("✓ Decompression successful!", fg="green"))
            app.echo(f"  Compressed size:   {format_size(compressed_size)}")
            app.echo(f"  Decompressed size: {format_size(decompressed_size)}")
            app.echo(f"  Time:              {format_time(elapsed)}")
            app.echo(f"  Speed:             {speed_mbs:.2f} MB/s")
            app.echo()

    except (Error, HeaderError, BackendError) as e:
        app.echo(app.style(f"✗ Decompression error: {e}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        app.echo(app.style(f"✗ Unexpected error: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["i", "info"])
def inspect(
    file: Path = app.Argument(..., help="File to inspect"),
    output_json: bool = app.Option(False, "--json", help="Output in JSON format"),
):
    """Inspect a compressed file and show metadata.

    Args:
        file (Path): The path to the compressed file.
        output_json (bool): If True, output metadata in JSON format.
    """
    try:
        result = inspect_file(file)

        if output_json:
            import json

            data = {
                "path": str(result.path),
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
            app.echo(json.dumps(data, indent=2))
            return

        app.echo(f"File: {result.path}")
        app.echo()

        if not result.is_compresso:
            app.echo(app.style("✗ Not a valid Compresso file", fg="red"))
            if result.reason:
                app.echo(f"  Reason: {result.reason}")
            sys.exit(1)

        if not result.header_ok:
            app.echo(app.style("✗ Invalid file header", fg="yellow"))
            if result.reason:
                app.echo(f"  Reason: {result.reason}")
            sys.exit(1)

        app.echo(app.style("✓ Valid Compresso file", fg="green"))
        app.echo()
        app.echo(f"Algorithm:       {result.algo_name or 'Unknown'} (ID: {result.algo_id})")
        app.echo(f"Version:         {result.version}")
        app.echo()

        if result.level is not None:
            app.echo(f"Level:           {result.level}")
        else:
            app.echo(f"Level:           auto")

        if result.orig_size:
            app.echo(f"Original size:   {format_size(result.orig_size)}")

        compressed_size = file.stat().st_size
        app.echo(f"Compressed size: {format_size(compressed_size)}")

        if result.orig_size:
            ratio = (compressed_size / result.orig_size) * 100
            app.echo(f"Compression:     {ratio:.1f}% of original")

        app.echo()
        app.echo(f"Backend available:  {'Yes' if result.backend_available else 'No'}")
        app.echo(f"Streaming support:  {'Yes' if result.has_streaming else 'No'}")
        app.echo(f"Can decompress:     {'Yes' if result.can_decompress else 'No'}")

        if result.estimated_decomp_s:
            app.echo(
                f"Est. decomp time:   {format_time(result.estimated_decomp_s)}"
            )

        if not result.can_decompress and result.reason:
            app.echo()
            app.echo(app.style(f"⚠ {result.reason}", fg="yellow"))

    except Exception as e:
        app.echo(app.style(f"✗ Error inspecting file: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["b", "bench"])
def benchmark(
    file: Path = app.Argument(..., help="File to benchmark"),
    algos: str | None = app.Option("all", "--algos", help="Comma-separated list of algorithms"),
    strategies: str | None = app.Option("all", "--strategies", help="Comma-separated list of strategies"),
    levels: str | None = app.Option("auto", "--levels", help="Comma-separated list of levels (0-9)"),
    repeats: int = app.Option(1, "--repeats", help="Number of times to repeat each benchmark"),
    temp_dir: Path | None = app.Option(None, "--temp-dir", help="Temporary directory for benchmark files"),
    update_cache: bool = app.Option(False, "--update-cache", help="Update speed estimates cache with benchmark results"),
):
    """Run compression benchmarks on a file.

    Args:
        file (Path): The path to the file to benchmark.
        algos (str | None): Comma-separated list of algorithms to use (default: all available).
        strategies (str | None): Comma-separated list of strategies to use (default: all).
        levels (str | None): Comma-separated list of levels to use (default: all).
        repeats (int): Number of times to repeat each benchmark (default: 1).
        temp_dir (Path | None): Temporary directory for benchmark files (default: None).
        update_cache (bool): If True, update the speed estimates cache with benchmark results (default: False).
    """
    try:
        algo_list = None
        if algos:
            algo_list = [a.strip() for a in algos.split(",") if a.strip()]

        strategy_list = None
        if strategies:
            strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]

        level_list = None
        if levels:
            level_list = []
            for level in levels.split(","):
                level = level.strip()
                if not level:
                    continue
                if level.lower() in ("auto", "default"):
                    level_list.append(None)
                else:
                    try:
                        level_list.append(int(level))
                    except ValueError:
                        app.echo(
                            app.style(
                                f"✗ Invalid level: {level}. Use integers 0-9 or 'auto'",
                                fg="red",
                            ),
                            err=True,
                        )
                        sys.exit(1)

        app.echo(f"Running benchmarks on: {file}")
        app.echo(f"Repeats: {repeats}")
        if algo_list:
            app.echo(f"Algorithms: {', '.join(algo_list)}")
        if strategy_list:
            app.echo(f"Strategies: {', '.join(strategy_list)}")
        if level_list is not None:
            level_str = ', '.join(str(l) if l is not None else 'auto' for l in level_list)
            app.echo(f"Levels: {level_str}")
        app.echo()

        results = benchmark_file(
            file,
            algos=algo_list,
            strategies=strategy_list,
            levels=level_list,
            repeats=repeats,
            temp_dir=temp_dir,
            update_cache=update_cache,
        )

        if not results:
            app.echo(app.style("✗ No benchmark results generated", fg="red"), err=True)
            sys.exit(1)

        print_results(results)

        if update_cache:
            app.echo()
            app.echo(app.style("✓ Speed cache updated", fg="green"))
            app.echo()

    except FileNotFoundError as e:
        app.echo(app.style(f"✗ File not found: {e}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        app.echo(app.style(f"✗ Benchmark error: {e}", fg="red"), err=True)
        sys.exit(1)


@app.command_with_aliases(aliases=["l", "ls"])
def list():
    """List all available compression algorithms."""
    try:
        caps = list_capabilities()

        if not caps:
            app.echo(app.style("✗ No compression backends available", fg="red"))
            sys.exit(1)

        app.echo(f"Available compression algorithms: {len(caps)}")
        app.echo()

        for cap in caps:
            app.echo(app.style(f"● {cap.name}", fg="green", bold=True))
            app.echo(f"  ID:              {cap.id}")
            app.echo(f"  Buffer mode:     {'Yes' if cap.has_buffer else 'No'}")
            app.echo(f"  Streaming mode:  {'Yes' if cap.has_stream else 'No'}")
            app.echo()

    except Exception as e:
        app.echo(app.style(f"✗ Error listing algorithms: {e}", fg="red"), err=True)
        sys.exit(1)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from .backend.benchmark import benchmark_file, print_results


def parse_csv(s: str | None) -> list[str]:
    """Parse a comma-separated string into a list of strings

    Args:
        s: Comma-separated string or None

    Returns:
        List of strings
    """
    if s is None:
        return []

    return [part.strip() for part in s.split(",") if part.strip()]


def parse_levels(s: str | None) -> list[int | None]:
    """Parse a comma-separated string into a list of levels (int or None)

    Args:
        s: Comma-separated string or None

    Returns:
        List of levels (int or None)
    """
    if not s:
        return []
    parts = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() in ("auto", "default"):
            parts.append(None)
        else:
            parts.append(int(part))

    return parts


def main(argv: list[str] | None = None):
    """Entry point for the compresso-benchmark CLI tool.
    
    Args:
        argv: List of command-line arguments (default: sys.argv)
    """
    parser = argparse.ArgumentParser(
        prog="compresso-bench",
        description="Run Compresso benchmarks on a file.",
    )
    parser.add_argument(
        "file",
        help="Input file to compress/decompress for benchmarking",
    )
    parser.add_argument(
        "--algos",
        type=str,
        help="Comma-separated list of algorithms to benchmark (default: all available)",
    )
    parser.add_argument(
        "--strategies",
        help="Comma-separated list of strategies to benchmark (default: all available)",
    )
    parser.add_argument(
        "--level",
        help="Comma-separated list of levels to benchmark (use 'auto' for automatic level)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to repeat each benchmark (default: 1)",
    )
    parser.add_argument(
        "--temp-dir",
        help="Temporary directory to use for benchmarking (default: system temp)",
    )
    parser.add_argument(
        "--update-cache",
        action="store_true",
        help="Update speed estimates cache with benchmark results",
    )

    args = parser.parse_args(argv)

    src = Path(args.file)
    if not src.is_file():
        parser.error(f"Input file does not exist: {src}")

    algos = (
        parse_csv(args.algos)
        if args.algos
        else ["zlib", "bzip2", "lzma", "zstd", "lz4", "snappy"]
    )
    strategies = (
        parse_csv(args.strategies) if args.strategies else ["fast", "balanced", "max_ratio"]
    )
    levels = parse_levels(args.level) if args.level else [None, 1, 3, 6, 9]

    results = benchmark_file(
        src,
        algos=algos,
        strategies=strategies,
        levels=levels,
        repeats=args.repeats,
        temp_dir=args.temp_dir,
        update_cache=args.update_cache,
    )

    print_results(results)


if __name__ == "__main__":
    main()

"""Benchmarking utilities for Compresso"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from tabulate import tabulate

from compressor import compress_file, decompress_file


@dataclass
class BenchmarkResult:
    """Holds the result of a single benchmark run
    
    Attributes:
        algo: Compression algorithm used
        strategy: Compression strategy used
        level: Compression level used
        compress_time: Time taken to compress the file (in seconds)
        decompress_time: Time taken to decompress the file (in seconds)
        input_size: Size of the input file (in bytes)
        compressed_size: Size of the compressed file (in bytes)
    """
    algo: str
    strategy: str
    level: Optional[int]
    compress_time: float
    decompress_time: float
    input_size: int
    compressed_size: int

    @property
    def ratio(self) -> float:
        """Compression ratio - smaller is better"""
        return self.compressed_size / self.input_size if self.input_size else 0.0
    
    @property
    def speed_mb_s(self) -> float:
        """Compression speed in MB/s"""
        return (self.input_size / (1024 * 1024)) / self.compress_time if self.compress_time else 0.0
    
    @property
    def decompress_speed_mb_s(self) -> float:
        """Decompression speed in MB/s"""
        return (self.input_size / (1024 * 1024)) / self.decompress_time if self.decompress_time else 0.0

def _safe_unlink(path: Path) -> None:
    """Remove a file if it exists

    Args:
        path: Path to the file to remove
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    
def benchmark_file(
    src: str | Path,
    *,
    algos: Iterable[str] | None = None,
    strategies: Iterable[str] | None = None,
    levels: Iterable[int | None] | None = None,
    repeats: int = 1,
    temp_dir: str | Path | None = None,
) -> List[BenchmarkResult]:
    """Benchmark compression and decompression on a single file
    
    Args:
        src: Path to the source file to benchmark
        algos: List of algorithms to benchmark. If None, all available algorithms are used.
        strategies: List of strategies to benchmark. If None, all available strategies are used.
        levels: List of compression levels to benchmark. If None, default levels are used.
        repeats: Number of times to repeat each benchmark for averaging
        temp_dir: Directory to use for temporary files. If None, system temp directory is used.
        
    Returns:
        List of BenchmarkResult objects with the results
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"Source file {src} does not exist or is not a file")

    if temp_dir is None:
        temp_dir = Path(os.getenv("TMPDIR", "/tmp"))

    if algos is None:
        algos = ['zlib', 'bzip2', 'lzma', 'zstd', 'lz4', 'snappy']

    if strategies is None:
        strategies = ['fast', 'balanced', 'max_ratio']

    if levels is None:
        levels = [None, 1, 3, 6, 9]

    temp_base = Path(temp_dir) if temp_dir else src.parent
    input_size = src.stat().st_size

    results: List[BenchmarkResult] = []

    for algo in algos:
        for strategy in strategies:
            for level in levels:
                comp_times: List[float] = []
                decomp_times: List[float] = []
                compressed_size: Optional[int] = None

                for _ in range(repeats):
                    with tempfile.NamedTemporaryFile(suffix=".comp", dir=temp_base, delete=False) as comp_file, \
                        tempfile.NamedTemporaryFile(suffix=".decomp", dir=temp_base, delete=False) as decomp_file:
                        comp_path = Path(comp_file.name)
                        decomp_path = Path(decomp_file.name)

                        # Compression
                        start_time = time.perf_counter()
                        compress_file(
                            str(src),
                            str(comp_path),
                            algo=algo,
                            strategy=strategy,
                            level=level,
                        )
                        comp_times.append(time.perf_counter() - start_time)

                        if compressed_size is None:
                            compressed_size = comp_path.stat().st_size
                        else:
                            sz = comp_path.stat().st_size
                            if sz != compressed_size:
                                print(f"Warning: Compressed size changed between runs: {compressed_size} vs {sz}")
                                compressed_size = sz

                        # Decompression
                        start_time = time.perf_counter()
                        decompress_file(str(comp_path), str(decomp_path))
                        decomp_times.append(time.perf_counter() - start_time)

                        # Verify
                        if not decomp_path.is_file() or decomp_path.stat().st_size != input_size:
                            print(f"Warning: Decompressed file size mismatch for {decomp_path}")

                        _safe_unlink(comp_path)
                        _safe_unlink(decomp_path)

                avg_comp_time = sum(comp_times) / repeats
                avg_decomp_time = sum(decomp_times) / repeats

                results.append(
                    BenchmarkResult(
                        algo=algo,
                        strategy=strategy,
                        level=level,
                        compress_time=avg_comp_time,
                        decompress_time=avg_decomp_time,
                        input_size=input_size,
                        compressed_size=compressed_size or 0,
                    )
                )

    return results

def print_results(results: List[BenchmarkResult]) -> None:
    """Print benchmark results in a tabular format
    
    Args:
        results: List of BenchmarkResult objects to print
    """
    if not results:
        print("No results to display.")
        return

    headers = [
        "Algo",
        "Strategy",
        "Level",
        "Comp Time (s)",
        "Decomp Time (s)",
        "MB/s (Comp)",
        "MB/s (Decomp)",
        "Ratio (comp/orig)",
    ]

    table_data = []
    for r in results:
        row = [
            r.algo,
            r.strategy,
            r.level if r.level is not None else "auto",
            f"{r.compress_time:.4f}",
            f"{r.decompress_time:.4f}",
            f"{r.speed_mb_s:.2f}",
            f"{r.decompress_speed_mb_s:.2f}",
            f"{r.ratio:.3f}",
        ]
        table_data.append(row)

    print(tabulate(table_data, headers=headers, tablefmt="grid"))


## Run benchmark if executed as a script

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Compresso benchmarks on a file.")
    parser.add_argument("src", type=str, help="Path to the source file to benchmark")
    parser.add_argument("--repeats", type=int, default=1, help="Number of times to repeat each benchmark")
    parser.add_argument("--temp-dir", type=str, default=None, help="Directory for temporary files")
    parser.add_argument("--algo", type=str, action="append", help="Compression algorithm to use (can specify multiple)")
    parser.add_argument("--strategy", type=str, action="append", help="Compression strategy to use (can specify multiple)")
    parser.add_argument("--level", type=int, action="append", help="Compression level to use (can specify multiple)")
    args = parser.parse_args()

    results = benchmark_file(
        args.src,
        algos=args.algo if args.algo else None,
        strategies=args.strategy if args.strategy else None,
        levels=args.level if args.level else None,
        repeats=args.repeats,
        temp_dir=args.temp_dir,
    )

    print_results(results)
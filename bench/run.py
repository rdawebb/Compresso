"""Times compress + decompress per format on a fixed mixed-data corpus.

Usage:
    python bench/run.py
    python bench/run.py --save build/bench/base.json
    python bench/run.py --compare build/bench/base.json --formats tar.zst,zip

Default is every format, 100 MB. Each perf change records a baseline with
`--save` before it and reports `--compare` after it; numbers are only comparable
on the same machine, and between runs with the same `--cool`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import shutil
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

from tabulate import tabulate

from compresso import _core
from compresso.cli._render.benchmark import benchmark_results
from compresso.introspect.benchmark import BenchmarkResult

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = REPO_ROOT / "build" / "bench"
TEXT_SOURCE = REPO_ROOT / "tests" / "fixtures" / "alice29.txt"

SEED = 20261010
BLOCK = 1024 * 1024

COMP_ALGOS = ("zlib", "bzip2", "lzma", "zstd", "lz4", "snappy")
STANDALONE_FORMATS = ("gz", "bz2", "xz", "zst", "lz4", "sz")
ARCHIVE_FORMATS = (
    "tar",
    "tar.gz",
    "tar.bz2",
    "tar.xz",
    "tar.zst",
    "tar.lz4",
    "tar.sz",
    "zip",
)


def mixed_block(rng: random.Random, kind: int, lines: list[bytes]) -> bytes:
    """Make one 1 MB block of text, binary records or noise.

    Args:
        rng: The corpus's seeded generator.
        kind: Which of the three kinds to make.
        lines: The prose the text blocks are shuffled from.

    Returns:
        Exactly `BLOCK` bytes.
    """
    if kind == 0:
        # Shuffled, so no block repeats an earlier one within an LZ window
        out = bytearray()
        while len(out) < BLOCK:
            out += rng.choice(lines)

        return bytes(out[:BLOCK])

    if kind == 1:
        # Fixed-width records with slowly changing fields, like a log or table
        base = rng.getrandbits(32)
        return b"".join(
            (base + i).to_bytes(4, "little")
            + rng.getrandbits(8).to_bytes(1, "little")
            + bytes(11)
            for i in range(BLOCK // 16)
        )

    return rng.randbytes(BLOCK)


def build_corpus(size_mb: int) -> tuple[Path, Path]:
    """Write the mixed file and the tree of files cut from it, unless cached.

    Args:
        size_mb: Total corpus size in MB.

    Returns:
        The single mixed file and the tree directory, both `size_mb` in total.
    """
    root = WORK_DIR / f"corpus-{size_mb}"
    mixed, tree, done = root / "mixed.bin", root / "tree", root / "done"
    if done.exists():
        return mixed, tree

    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    rng = random.Random(SEED)
    lines = TEXT_SOURCE.read_bytes().splitlines(keepends=True)
    with mixed.open("wb") as out:
        for i in range(size_mb):
            out.write(mixed_block(rng, i % 3, lines))

    # Log-uniform sizes from 512 B to 2 MB, two directory levels deep, so the
    # archives exercise per-entry overhead and the source walk too
    data = mixed.read_bytes()
    offset = n = 0
    while offset < len(data):
        size = int(2 ** rng.uniform(9, 21))
        path = tree / f"d{n % 8}" / f"e{n % 5}" / f"f{n:05}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data[offset : offset + size])
        offset += size
        n += 1

    # Last, so an interrupted build is redone rather than reused
    done.touch()

    return mixed, tree


def timed(call: Callable[[], object], cool: float) -> float:
    """Time one call, then settle the machine before the next.

    What the call wrote is flushed to disk, untimed, so the write-back doesn't
    land in the next call's timing; then it rests for `cool` times as long as
    the call took, so a machine that throttles when hot doesn't time the next
    call at whatever heat this one left.

    Args:
        call: The operation to time.
        cool: Rest per second of work; 0 skips resting.

    Returns:
        The call's duration in seconds.
    """
    start = time.perf_counter()
    call()
    elapsed = time.perf_counter() - start
    if hasattr(os, "sync"):  # Not on Windows
        os.sync()
    time.sleep(elapsed * cool)

    return elapsed


def remove(path: Path) -> None:
    """Delete a file or directory left by an earlier run, if any.

    Args:
        path: The path to delete.
    """
    if path.is_dir():
        shutil.rmtree(path)

    else:
        path.unlink(missing_ok=True)


def time_pair(
    name: str,
    size: int,
    compress: Callable[[], object],
    decompress: Callable[[], object],
    packed: Path,
    unpacked: Path,
    repeats: int,
    cool: float,
) -> BenchmarkResult:
    """Time one format's compress and decompress, keeping the fastest run.

    Args:
        name: The format, as reported.
        size: Input bytes, for the MB/s figures.
        compress: Writes `packed`.
        decompress: Reads `packed` into `unpacked`.
        packed: The compressed output.
        unpacked: The decompressed output, a file or a directory.
        repeats: Runs per side; the minimum is least disturbed by other load.
        cool: Rest after each timed call, per second it took.

    Returns:
        The result, with the fastest compress and decompress times.
    """
    comp_times: list[float] = []
    decomp_times: list[float] = []
    for _ in range(repeats):
        # Outside the timing, so replacing a previous output isn't counted
        packed.unlink(missing_ok=True)
        comp_times.append(timed(compress, cool))

        remove(unpacked)
        decomp_times.append(timed(decompress, cool))

    compressed_size = packed.stat().st_size
    packed.unlink()
    remove(unpacked)

    return BenchmarkResult(
        algo=name,
        strategy="-",
        level=None,
        compress_time=min(comp_times),
        decompress_time=min(decomp_times),
        input_size=size,
        compressed_size=compressed_size,
    )


def run(
    formats: list[str], size_mb: int, repeats: int, cool: float
) -> list[BenchmarkResult]:
    """Benchmark each format on the corpus at its default level.

    Args:
        formats: `comp:<algo>`, standalone and archive format names.
        size_mb: Corpus size in MB.
        repeats: Runs per format.
        cool: Rest after each timed call, per second it took.

    Returns:
        One result per format, in the order given.
    """
    mixed, tree = build_corpus(size_mb)
    size = mixed.stat().st_size
    work = WORK_DIR / "work"
    work.mkdir(parents=True, exist_ok=True)
    src, unpacked = str(mixed), work / "unpacked"

    results: list[BenchmarkResult] = []
    for fmt in formats:
        # Suffixed, since extraction tells a compressed tar from a standalone
        # stream by its name
        packed = work / ("packed.comp" if fmt.startswith("comp:") else f"packed.{fmt}")
        if fmt.startswith("comp:"):
            algo = fmt.removeprefix("comp:")
            compress = partial(
                _core.compress_file, src, str(packed), algo, "balanced", -1, overwrite=2
            )
            decompress = partial(
                _core.decompress_file, str(packed), str(unpacked), "", overwrite=2
            )

        elif fmt in ARCHIVE_FORMATS:
            compress = partial(
                _core.create_archive, str(packed), fmt, [str(tree)], overwrite=2
            )
            decompress = partial(_core.extract_archive, str(packed), str(unpacked), [])

        else:
            compress = partial(
                _core.compress_standalone, src, str(packed), fmt, overwrite=2
            )
            decompress = partial(
                _core.decompress_standalone,
                str(packed),
                str(unpacked),
                fmt,
                overwrite=2,
            )

        result = time_pair(
            fmt, size, compress, decompress, packed, unpacked, repeats, cool
        )
        print(f"{fmt:<12} {result.comp_mb_s:8.1f} / {result.decomp_mb_s:8.1f} MB/s")
        results.append(result)

    return results


def compare(results: list[BenchmarkResult], baseline_path: Path) -> None:
    """Print each format's speed and size change against a saved run.

    Args:
        results: This run's results.
        baseline_path: A file written by `--save`.
    """
    baseline = {
        r.algo: r
        for r in (
            BenchmarkResult(**row) for row in json.loads(baseline_path.read_text())
        )
    }

    def delta(new: float, old: float) -> str:
        return f"{(new - old) / old:+.1%}" if old else "-"

    rows = [
        [
            r.algo,
            delta(r.comp_mb_s, old.comp_mb_s),
            delta(r.decomp_mb_s, old.decomp_mb_s),
            delta(r.compressed_size, old.compressed_size),
        ]
        for r in results
        if (old := baseline.get(r.algo))
    ]

    print(tabulate(rows, headers=["Format", "Comp MB/s", "Decomp MB/s", "Size"]))


def main() -> None:
    """Parse the arguments, run the benchmark and report it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    every = [f"comp:{a}" for a in COMP_ALGOS] + [
        *STANDALONE_FORMATS,
        *ARCHIVE_FORMATS,
    ]
    parser.add_argument(
        "--formats",
        default=",".join(every),
        help="comma-separated; comp:<algo>, standalone or archive names",
    )
    parser.add_argument("--size-mb", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--cool",
        type=float,
        default=1.0,
        help="rest after each timed call, per second it took; 0 to skip",
    )
    parser.add_argument("--save", type=Path, help="write the results as JSON")
    parser.add_argument("--compare", type=Path, help="a --save file to diff against")
    args = parser.parse_args()

    results = run(args.formats.split(","), args.size_mb, args.repeats, args.cool)
    benchmark_results(results)

    if args.compare:
        compare(results, args.compare)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(
            json.dumps([dataclasses.asdict(r) for r in results], indent=2)
        )


if __name__ == "__main__":
    main()

"""What a compress, archive, extract or decompress job prints before and after."""

from __future__ import annotations

from pathlib import Path

from ...frontend.api import CompressionPlan, DecompressionPlan
from ...frontend.archive_api import ArchivePlan, ExtractPlan
from . import format_size, format_time, succeed


def compress_plan(plan: CompressionPlan) -> None:
    """Print what a single-file compression is about to do.

    Args:
        plan: The planned compression.
    """
    lines: list[str] = [
        f"Compressing: {plan.src}",
        f"Output:      {plan.dest}",
        f"Algorithm:   {plan.backend_name}",
    ]

    if not plan.options.format:
        lines.append(f"Strategy:    {plan.options.strategy}")

    if plan.options.level is not None:
        lines.append(f"Level:       {plan.options.level}")

    print("\n".join(lines) + "\n")


def compress_result(plan: CompressionPlan, written: Path, elapsed: float) -> None:
    """Print a finished single-file compression's stats.

    Args:
        plan: The plan as it was before the job ran.
        written: The path actually written, which a rename may have moved.
        elapsed: Seconds the job took.
    """
    compressed_size: int = written.stat().st_size
    ratio: int | float = (
        (compressed_size / plan.input_size) * 100 if plan.input_size > 0 else 0
    )
    speed_mbs: int | float = (
        (plan.input_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
    )

    print()
    succeed("Compression successful!\n")
    if written != plan.dest:
        print(f"  Renamed to:      {written}")

    print(
        f"  Original size:   {format_size(size_bytes=plan.input_size)}\n"
        f"  Compressed size: {format_size(size_bytes=compressed_size)}\n"
        f"  Ratio:           {ratio:.1f}% of original\n"
        f"  Time:            {format_time(seconds=elapsed)}\n"
        f"  Speed:           {speed_mbs:.2f} MB/s\n"
        f"  Saved:           {format_size(size_bytes=plan.input_size - compressed_size)}\n"
    )


def archive_plan(plan: ArchivePlan) -> None:
    """Print what an archive job is about to do.

    Args:
        plan: The planned archive.
    """
    print(
        f"Archiving:   {plan.entry_count} source(s)\n"
        f"Output:      {plan.output}\n"
        f"Format:      {plan.options.format}\n"
        f"Input size:  {format_size(size_bytes=plan.total_input_size)}\n"
    )


def archive_result(plan: ArchivePlan, written: Path, elapsed: float) -> None:
    """Print a finished archive's stats.

    Args:
        plan: The plan as it was before the job ran.
        written: The path actually written, which a rename may have moved.
        elapsed: Seconds the job took.
    """
    print()
    succeed("Archive created!\n")
    if written != plan.output:
        print(f"  Renamed to:   {written}")

    print(
        f"  Input size:   {format_size(size_bytes=plan.total_input_size)}\n"
        f"  Archive size: {format_size(size_bytes=written.stat().st_size)}\n"
        f"  Time:         {format_time(seconds=elapsed)}\n"
    )


def extract_plan(plan: ExtractPlan) -> None:
    """Print what an extraction is about to do.

    Args:
        plan: The planned extraction.
    """
    print(
        f"Extracting: {plan.archive}\n"
        f"Output dir: {plan.output_dir}\n"
        f"Entries:    {len(plan.entries)}\n"
    )


def extract_result(plan: ExtractPlan, elapsed: float) -> None:
    """Print a finished extraction's stats.

    Args:
        plan: The extraction's plan.
        elapsed: Seconds the job took.
    """
    print()
    succeed("Extraction successful!\n")
    print(
        f"  Entries: {len(plan.entries)}\n  Time:    {format_time(seconds=elapsed)}\n"
    )


def decompress_plan(plan: DecompressionPlan) -> None:
    """Print what a single-file decompression is about to do.

    Args:
        plan: The planned decompression.
    """
    lines: list[str] = [
        f"Decompressing: {plan.src}",
        f"Output:        {plan.dest}",
        f"Format:        {plan.format or plan.inspection.algo_name}",
    ]
    if plan.inspection.orig_size:
        lines.append(
            f"Original size: {format_size(size_bytes=plan.inspection.orig_size)}"
        )

    print("\n".join(lines) + "\n")


def decompress_result(
    plan: DecompressionPlan, written: Path, compressed_size: int, elapsed: float
) -> None:
    """Print a finished single-file decompression's stats.

    Args:
        plan: The plan as it was before the job ran.
        written: The path actually written, which a rename may have moved.
        compressed_size: The source's size in bytes.
        elapsed: Seconds the job took.
    """
    decompressed_size: int = written.stat().st_size
    speed_mbs: int | float = (
        (decompressed_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
    )

    succeed("Decompression successful!\n")
    if written != plan.dest:
        print(f"  Renamed to:        {written}")

    print(
        f"  Compressed size:   {format_size(size_bytes=compressed_size)}\n"
        f"  Decompressed size: {format_size(size_bytes=decompressed_size)}\n"
        f"  Time:              {format_time(seconds=elapsed)}\n"
        f"  Speed:             {speed_mbs:.2f} MB/s\n"
    )

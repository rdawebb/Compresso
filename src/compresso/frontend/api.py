"""API for the frontend compression and decompression operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import compresso as comp

from ..backend.capabilities import list_capabilities
from ..backend.file_inspect import InspectResult
from ..backend.file_inspect import inspect as inspect_file
from ..backend.speeds import get_estimated_speeds

MB = 1024 * 1024


@dataclass(frozen=True)
class CompressionOptions:
    """User-facing compression options.

    Attributes:
        algo (Optional[str]): Compression algorithm name, or None for auto.
        strategy (str): Compression strategy - "fast", "balanced", or "max_ratio".
        level (Optional[int]): Compression level (0-9), or None for auto.
    """

    algo: Optional[str] = None
    strategy: str = "balanced"
    level: Optional[int] = None


@dataclass(frozen=True)
class CompressionPlan:
    """Holds a compression plan based on user options and file characteristics.

    Attributes:
        src (Path): Source file path.
        dest (Path): Destination file path.
        options (CompressionOptions): User-defined compression options.
        input_size (int): Size of the input file in bytes.
        backend_name (Optional[str]): Selected backend name, or None if unavailable.
        estimated_seconds (Optional[float]): Estimated compression time in seconds, or None if unavailable.
        can_compress (bool): Whether compression can proceed with the selected options.
        reason_if_unavailable (Optional[str]): Reason why compression cannot proceed, if applicable.
    """

    src: Path
    dest: Path
    options: CompressionOptions

    input_size: int
    backend_name: Optional[str]
    estimated_seconds: Optional[float]
    can_compress: bool
    reason_if_unavailable: Optional[str]


@dataclass(frozen=True)
class DecompressionPlan:
    """Holds a decompression plan based on file inspection.

    Attributes:
        src (Path): Source file path.
        dest (Path): Destination file path.
        inspection (InspectResult): Result of the file inspection.
        estimated_seconds (Optional[float]): Estimated decompression time in seconds, or None if unavailable.
    """

    src: Path
    dest: Path

    inspection: InspectResult
    estimated_seconds: Optional[float]


def _choose_backend_for_strategy(strategy: str) -> Optional[str]:
    """Approximate the algo the backend will choose for a given strategy.

    Args:
        strategy (str): Compression strategy - "fast", "balanced", or "max_ratio".

    Returns:
        Optional[str]: The chosen backend name, or None if no suitable backend is found.
    """
    strategy = (strategy or "balanced").lower()
    caps = list_capabilities()
    by_name = {cap.name: cap for cap in caps}

    def first_available(names) -> Optional[str]:
        for name in names:
            cap = by_name.get(name)
            if cap is not None:
                return name
        return None

    if strategy == "fast":
        return first_available(["lz4", "snappy", "zstd", "zlib", "bzip2", "lzma"])

    if strategy == "max_ratio":
        return first_available(["bzip2", "lzma", "zstd", "zlib", "lz4", "snappy"])

    # Balanced
    return first_available(["zstd", "zlib", "bzip2", "lzma", "lz4", "snappy"])


def plan_compression(
    src: str | Path,
    dest: Optional[str | Path] = None,
    options: Optional[CompressionOptions] = None,
) -> CompressionPlan:
    """Plan a compression operation based on user options and file characteristics.

    Args:
        src (str | Path): Source file path.
        dest (Optional[str | Path]): Destination file path. If None, appends ".comp" to source.
        options (Optional[CompressionOptions]): User-defined compression options. If None, defaults are used.

    Returns:
        CompressionPlan: The resulting compression plan.
    """
    src_path = Path(src)
    if dest is None:
        dest_path = src_path.with_suffix(src_path.suffix + ".comp")
    else:
        dest_path = Path(dest)

    if options is None:
        options = CompressionOptions()

    if not src_path.is_file():
        return CompressionPlan(
            src=src_path,
            dest=dest_path,
            options=options,
            input_size=0,
            backend_name=None,
            estimated_seconds=None,
            can_compress=False,
            reason_if_unavailable="Source file does not exist or is not a file",
        )

    input_size = src_path.stat().st_size

    if options.algo:
        backend_name = options.algo.lower()
    else:
        backend_name = _choose_backend_for_strategy(options.strategy)

    if backend_name is None:
        return CompressionPlan(
            src=src_path,
            dest=dest_path,
            options=options,
            input_size=input_size,
            backend_name=None,
            estimated_seconds=None,
            can_compress=False,
            reason_if_unavailable="No suitable backend found for the selected strategy",
        )

    mb_s = get_estimated_speeds(backend_name, operation="compress")
    estimated_seconds = (input_size / MB) / mb_s if input_size > 0 else 0.0

    return CompressionPlan(
        src=src_path,
        dest=dest_path,
        options=options,
        input_size=input_size,
        backend_name=backend_name,
        estimated_seconds=estimated_seconds,
        can_compress=True,
        reason_if_unavailable=None,
    )


def plan_decompression(
    src: str | Path, dest: Optional[str | Path] = None
) -> DecompressionPlan:
    """Plan a decompression operation based on file inspection.

    Args:
        src (str | Path): Source file path.
        dest (Optional[str | Path]): Destination file path. If None, removes
            ".comp" suffix from source if present.

    Returns:
        DecompressionPlan: The resulting decompression plan.
    """
    src_path = Path(src)
    if dest is None:
        if src_path.suffix:
            dest_path = src_path.with_suffix("")
        else:
            dest_path = src_path.with_suffix(".out")
    else:
        dest_path = Path(dest)

    inspection = inspect_file(src_path)
    est_seconds = inspection.estimated_decomp_s

    return DecompressionPlan(
        src=src_path,
        dest=dest_path,
        inspection=inspection,
        estimated_seconds=est_seconds,
    )


@dataclass
class JobResult:
    """Holds the result of a compression or decompression job."""

    ok: bool
    error: Optional[BaseException]
    plan: CompressionPlan | DecompressionPlan


ProgressCallback = Callable[[float, int, int], None]


class CompressionJob:
    """Compression job high-level wrapper."""

    def __init__(self, plan: CompressionPlan):
        self.plan = plan

    @classmethod
    def from_file(
        cls,
        src: str | Path,
        dest: Optional[str | Path] = None,
        options: Optional[CompressionOptions] = None,
    ) -> "CompressionJob":
        """Create a CompressionJob from file paths and options."""
        return cls(plan_compression(src, dest, options))

    def run(self, progress: Optional[ProgressCallback] = None) -> JobResult:
        """Run the compression job.

        Args:
            progress (Optional[ProgressCallback]): Optional progress callback.

        Returns:
            JobResult: The result of the compression job.
        """
        if not self.plan.can_compress:
            return JobResult(
                ok=False,
                error=RuntimeError(
                    self.plan.reason_if_unavailable or "Cannot compress"
                ),
                plan=self.plan,
            )

        total = self.plan.input_size
        try:
            if progress:
                progress(0.0, 0, total)

            comp.compress_file(
                self.plan.src,
                self.plan.dest,
                algo=self.plan.options.algo,
                strategy=self.plan.options.strategy,
                level=self.plan.options.level,
            )

            if progress:
                progress(1.0, total, total)

            return JobResult(
                ok=True,
                error=None,
                plan=self.plan,
            )
        except BaseException as e:
            return JobResult(
                ok=False,
                error=e,
                plan=self.plan,
            )


class DecompressionJob:
    """Decompression job high-level wrapper."""

    def __init__(self, plan: DecompressionPlan):
        self.plan = plan

    @classmethod
    def from_file(
        cls, src: str | Path, dest: Optional[str | Path] = None
    ) -> "DecompressionJob":
        """Create a DecompressionJob from file paths."""
        return cls(plan_decompression(src, dest))

    def run(self, progress: Optional[ProgressCallback] = None) -> JobResult:
        """Run the decompression job.

        Args:
            progress (Optional[ProgressCallback]): Optional progress callback.

        Returns:
            JobResult: The result of the decompression job.
        """
        insp = self.plan.inspection

        if not insp.is_compresso:
            return JobResult(
                ok=False,
                error=RuntimeError("Source file is not a Compresso archive"),
                plan=self.plan,
            )

        if not insp.header_ok:
            return JobResult(
                ok=False,
                error=RuntimeError("Compresso file header is invalid"),
                plan=self.plan,
            )

        if not insp.can_decompress:
            return JobResult(
                ok=False,
                error=RuntimeError("No available backend can decompress this file"),
                plan=self.plan,
            )

        total = insp.orig_size or 0
        try:
            if progress:
                progress(0.0, 0, total)

            comp.decompress_file(
                self.plan.src,
                self.plan.dest,
            )

            if progress:
                progress(1.0, total, total)

            return JobResult(
                ok=True,
                error=None,
                plan=self.plan,
            )
        except BaseException as e:
            return JobResult(
                ok=False,
                error=e,
                plan=self.plan,
            )

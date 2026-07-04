"""API for the frontend compression and decompression operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .._core import compress_file, decompress_file
from .._core import get_default_backend_for_strategy as default_backend
from ..backend.file_inspect import InspectResult
from ..backend.file_inspect import inspect as inspect_file
from ..backend.speeds import get_estimated_speeds
from ._job import JobResult, ProgressCallback

MB = 1024 * 1024


@dataclass(frozen=True)
class CompressionOptions:
    """User-facing compression options.

    Attributes:
        algo: Compression algorithm name, or None for auto.
        strategy: Compression strategy - "fast", "balanced", or "max_ratio".
        level: Compression level (0-9), or None for auto.
    """

    algo: str | None = None
    strategy: str = "balanced"
    level: int | None = None


@dataclass(frozen=True)
class CompressionPlan:
    """Holds a compression plan based on user options and file characteristics.

    Attributes:
        src: Source file path.
        dest: Destination file path.
        options: User-defined compression options.
        input_size: Size of the input file in bytes.
        backend_name: Selected backend name, or None if unavailable.
        estimated_seconds: Estimated compression time in seconds, or None if unavailable.
        can_compress: Whether compression can proceed with the selected options.
        reason_if_unavailable: Reason why compression cannot proceed, if applicable.
    """

    src: Path
    dest: Path
    options: CompressionOptions

    input_size: int
    backend_name: str | None
    estimated_seconds: float | None
    can_compress: bool
    reason_if_unavailable: str | None


@dataclass(frozen=True)
class DecompressionPlan:
    """Holds a decompression plan based on file inspection.

    Attributes:
        src: Source file path.
        dest: Destination file path.
        inspection: Result of the file inspection.
        estimated_seconds: Estimated decompression time in seconds, or None if unavailable.
    """

    src: Path
    dest: Path

    inspection: InspectResult
    estimated_seconds: float | None


def plan_compression(
    src: str | Path,
    dest: str | Path | None = None,
    options: CompressionOptions | None = None,
) -> CompressionPlan:
    """Plan a compression operation based on user options and file characteristics.

    Args:
        src: Source file path.
        dest: Destination file path. If None, appends ".comp" to source.
        options: User-defined compression options. If None, defaults are used.

    Returns:
        CompressionPlan: The resulting compression plan.
    """
    src_path = Path(src)
    if dest is None:
        dest_path: Path = src_path.with_suffix(suffix=src_path.suffix + ".comp")

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

    input_size: int = src_path.stat().st_size

    if options.algo:
        backend_name: str = options.algo.lower()

    else:
        backend_name: str | None = default_backend(options.strategy or "balanced")

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

    mb_s: int | float = get_estimated_speeds(algo=backend_name, operation="compress")
    estimated_seconds: int | float = (input_size / MB) / mb_s if input_size > 0 else 0.0

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
    src: str | Path, dest: str | Path | None = None
) -> DecompressionPlan:
    """Plan a decompression operation based on file inspection.

    Args:
        src: Source file path.
        dest: Destination file path. If None, removes
            ".comp" suffix from source if present.

    Returns:
        DecompressionPlan: The resulting decompression plan.
    """
    src_path = Path(src)
    if dest is None:
        if src_path.suffix:
            dest_path: Path = src_path.with_suffix(suffix="")

        else:
            dest_path: Path = src_path.with_suffix(suffix=".out")

    else:
        dest_path = Path(dest)

    inspection: InspectResult = inspect_file(path=src_path)
    est_seconds: int | float | None = inspection.estimated_decomp_s

    return DecompressionPlan(
        src=src_path,
        dest=dest_path,
        inspection=inspection,
        estimated_seconds=est_seconds,
    )


class CompressionJob:
    """Compression job high-level wrapper."""

    def __init__(self, plan: CompressionPlan) -> None:
        """Initialise the compression job.

        Args:
            plan: The compression plan to execute.
        """
        self.plan: CompressionPlan = plan

    @classmethod
    def from_file(
        cls,
        src: str | Path,
        dest: str | Path | None = None,
        options: CompressionOptions | None = None,
    ) -> CompressionJob:
        """Create a CompressionJob from file paths and options.

        Args:
            src: Source file path.
            dest: Destination file path. If None, defaults to appending ".comp" to the source.
            options: User-defined compression options. If None, defaults are used.

        Returns:
            CompressionJob: The created compression job.
        """
        return cls(plan=plan_compression(src, dest, options))

    def run(self, progress: ProgressCallback | None = None) -> JobResult:
        """Run the compression job.

        Args:
            progress: Optional progress callback.

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

        total: int = self.plan.input_size
        try:
            if progress:
                progress(0.0, 0, total)

            lvl: int = (
                -1 if self.plan.options.level is None else int(self.plan.options.level)
            )

            compress_file(
                src_path=str(object=self.plan.src),
                dst_path=str(object=self.plan.dest),
                algo=self.plan.backend_name or "",
                strategy=self.plan.options.strategy or "",
                level=lvl,
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

    def __init__(self, plan: DecompressionPlan) -> None:
        """Initialise the decompression job.

        Args:
            plan: The decompression plan to execute.
        """
        self.plan: DecompressionPlan = plan

    @classmethod
    def from_file(
        cls, src: str | Path, dest: str | Path | None = None
    ) -> DecompressionJob:
        """Create a DecompressionJob from file paths.

        Args:
            src: Source file path.
            dest: Destination file path. If None, defaults to the source path.

        Returns:
            DecompressionJob: The created decompression job.
        """
        return cls(plan=plan_decompression(src, dest))

    def run(self, progress: ProgressCallback | None = None) -> JobResult:
        """Run the decompression job.

        Args:
            progress: Optional progress callback.

        Returns:
            JobResult: The result of the decompression job.
        """
        insp: InspectResult = self.plan.inspection

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

            decompress_file(
                src_path=str(object=self.plan.src),
                dst_path=str(object=self.plan.dest),
                algo="",
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

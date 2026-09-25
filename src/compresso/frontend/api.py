"""API for the frontend compression and decompression operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

from .._core import (
    Cancelled,
    CancelToken,
    check_level,
    compress_file,
    compress_standalone,
    decompress_file,
    decompress_standalone,
    detect_format,
    format_is_archive,
)
from .._core import get_default_backend_for_strategy as default_backend
from .._levels import to_core_level
from ..backend.file_inspect import InspectResult
from ..backend.file_inspect import inspect as inspect_file
from ..backend.speeds import get_estimated_speeds
from ._job import JobResult, ProgressCallback, ThreadedJob, to_core_progress
from .archive_api import _OVERWRITE_CODES, OverwriteMode

MB = 1024 * 1024

# Single-file container formats, as opposed to the Compresso container or an archive
STANDALONE_FORMATS: dict[str, str] = {
    "gz": "gzip",
    "gzip": "gzip",
    "bz2": "bzip2",
    "bzip2": "bzip2",
    "xz": "xz",
    "lzma": "xz",
    "zst": "zstd",
    "zstd": "zstd",
    "lz4": "lz4",
}

# Suffix each standalone format conventionally uses, for naming an output the
# caller did not name themselves
STANDALONE_SUFFIXES: dict[str, str] = {
    "gzip": ".gz",
    "bzip2": ".bz2",
    "xz": ".xz",
    "zstd": ".zst",
    "lz4": ".lz4",
}


def canonical_standalone_format(name: str | None) -> str | None:
    """Return the canonical name for a single-file format, or None.

    Args:
        name: A format as the user wrote it, e.g. "gz".

    Returns:
        The canonical name (e.g. "gzip"), or None if it is not a single-file
        format — which includes None, archive formats and unknown names.
    """
    if not name:
        return None
    return STANDALONE_FORMATS.get(name.lower())


@dataclass(frozen=True)
class CompressionOptions:
    """User-facing compression options.

    Attributes:
        algo: Compression algorithm name, or None for auto.
        strategy: Compression strategy - "fast", "balanced", or "max_ratio".
        level: Compression level, or None for the backend's default; the
            range depends on the backend (see `BackendCapabilities`).
        format: Single-file container to write, e.g. "gz". None writes the
            Compresso container, which is the only one that records the
            algorithm used; a standalone format is chosen by `algo` instead and
            ignores `strategy`.
        overwrite: What to do when the destination file already exists.
    """

    algo: str | None = None
    strategy: str = "balanced"
    level: int | None = None
    format: str | None = None
    overwrite: OverwriteMode = OverwriteMode.RENAME


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
        inspection: Result of the file inspection. Only meaningful for the
            Compresso container; a standalone file simply is not one.
        estimated_seconds: Estimated decompression time in seconds, or None if unavailable.
        format: Canonical name of the standalone container detected, or None
            when the source is a Compresso file.
        can_run: Whether decompression can proceed.
        reason_if_unavailable: Reason it cannot, if applicable.
    """

    src: Path
    dest: Path

    inspection: InspectResult
    estimated_seconds: float | None
    format: str | None = None
    can_run: bool = True
    reason_if_unavailable: str | None = None


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

    if options is None:
        options = CompressionOptions()

    # A standalone format takes its own suffix; otherwise the Compresso one
    standalone: str | None = canonical_standalone_format(options.format)
    suffix: str = STANDALONE_SUFFIXES[standalone] if standalone else ".comp"

    if dest is None:
        dest_path: Path = src_path.with_suffix(suffix=src_path.suffix + suffix)

    else:
        dest_path = Path(dest)

    try:
        # Plain strings still work at runtime
        options = replace(options, overwrite=OverwriteMode(options.overwrite))

    except ValueError:
        return CompressionPlan(
            src=src_path,
            dest=dest_path,
            options=options,
            input_size=0,
            backend_name=None,
            estimated_seconds=None,
            can_compress=False,
            reason_if_unavailable=(
                f"Unknown overwrite mode: {options.overwrite!r} "
                f"(expected one of {', '.join(OverwriteMode)})"
            ),
        )

    if options.format and standalone is None:
        return CompressionPlan(
            src=src_path,
            dest=dest_path,
            options=options,
            input_size=0,
            backend_name=None,
            estimated_seconds=None,
            can_compress=False,
            reason_if_unavailable=(
                f"Not a single-file format: {options.format} "
                f"(expected one of {', '.join(sorted(STANDALONE_FORMATS))})"
            ),
        )

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

    if standalone:
        # The container dictates the codec, so there is nothing to choose
        backend_name: str | None = standalone

    elif options.algo:
        backend_name = options.algo.lower()

    else:
        backend_name = default_backend(options.strategy or "balanced")

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

    if options.level is not None:
        try:
            # The core's own check, so each backend's range lives in one place
            if standalone:
                check_level(options.level, format=standalone)
            else:
                check_level(options.level, algo=backend_name)

        except ValueError as e:
            return CompressionPlan(
                src=src_path,
                dest=dest_path,
                options=options,
                input_size=input_size,
                backend_name=backend_name,
                estimated_seconds=None,
                can_compress=False,
                reason_if_unavailable=str(e),
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
        dest: Destination file path; if None, the source path with its last
            suffix removed (`notes.txt.gz` becomes `notes.txt`), or with
            `.out` appended when it has no suffix.

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

    def plan(
        fmt: str | None, can_run: bool, reason: str | None = None
    ) -> DecompressionPlan:
        return DecompressionPlan(
            src=src_path,
            dest=dest_path,
            inspection=inspection,
            estimated_seconds=est_seconds,
            format=fmt,
            can_run=can_run,
            reason_if_unavailable=reason,
        )

    if not src_path.is_file():
        return plan(None, False, "Source file does not exist or is not a file")

    # Decided from the file's magic bytes rather than its name, so a renamed
    # archive is still recognised correctly
    try:
        detected: str = detect_format(str(object=src_path))

    except Exception as e:  # noqa: BLE001 - surface as an unavailable plan
        return plan(None, False, f"Cannot read file: {e}")

    if detected in STANDALONE_SUFFIXES:
        return plan(detected, True)

    if format_is_archive(detected):
        return plan(
            None, False, f"{src_path.name} is a {detected} archive; extract it instead"
        )

    if not inspection.is_compresso:
        return plan(
            None, False, inspection.reason or "Not a recognised compressed file"
        )

    if not inspection.header_ok:
        return plan(
            None, False, inspection.reason or "Compresso file header is invalid"
        )

    if not inspection.can_decompress:
        return plan(
            None,
            False,
            inspection.reason or "No available backend can decompress this file",
        )

    return plan(None, True)


class CompressionJob(ThreadedJob[CompressionPlan]):
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
    ) -> Self:
        """Create a CompressionJob from file paths and options.

        Args:
            src: Source file path.
            dest: Destination file path. If None, defaults to appending ".comp" to the source.
            options: User-defined compression options. If None, defaults are used.

        Returns:
            CompressionJob: The created compression job.
        """
        return cls(plan=plan_compression(src, dest, options))

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Run the compression job.

        A destination that already exists is handled per `plan.options.overwrite`
        (default `RENAME`, the same numbered-sibling behavior archiving and
        extraction use); when that renames the file, `self.plan.dest` (and the
        plan on the returned `JobResult`) is updated to the path actually
        written.

        Args:
            progress: Optional progress callback, invoked with
                `(fraction, done_bytes, total_bytes)`; raising aborts the job
                and the exception is reported through the returned `JobResult`.
            cancel: Optional `CancelToken`.

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
            lvl: int = to_core_level(self.plan.options.level)

            standalone: str | None = canonical_standalone_format(
                self.plan.options.format
            )

            if standalone:
                # A standalone container has no Compresso header to record the
                # algorithm in, so the format itself is the codec
                actual_path = compress_standalone(
                    input_path=str(object=self.plan.src),
                    output_path=str(object=self.plan.dest),
                    format=standalone,
                    compression_level=lvl,
                    overwrite=_OVERWRITE_CODES[self.plan.options.overwrite],
                    progress=to_core_progress(progress, total),
                    cancel=cancel,
                )

            else:
                actual_path = compress_file(
                    src_path=str(object=self.plan.src),
                    dst_path=str(object=self.plan.dest),
                    algo=self.plan.backend_name or "",
                    strategy=self.plan.options.strategy or "",
                    level=lvl,
                    overwrite=_OVERWRITE_CODES[self.plan.options.overwrite],
                    progress=to_core_progress(progress, total),
                    cancel=cancel,
                )

            # RENAME may have picked a different name than what was requested;
            # keep `self.plan.dest` truthful for callers that inspect it after
            # a successful run
            self.plan = replace(self.plan, dest=Path(actual_path))
            return JobResult(
                ok=True,
                error=None,
                plan=self.plan,
            )

        # Cancellation is a deliberate stop, so it is reported through its own
        # flag with no error attached
        except Cancelled:
            return JobResult(
                ok=False,
                error=None,
                plan=self.plan,
                cancelled=True,
            )

        # `run` reports failure through JobResult rather than raising
        except Exception as e:  # noqa: BLE001
            return JobResult(
                ok=False,
                error=e,
                plan=self.plan,
            )


class DecompressionJob(ThreadedJob[DecompressionPlan]):
    """Decompression job high-level wrapper."""

    def __init__(self, plan: DecompressionPlan) -> None:
        """Initialise the decompression job.

        Args:
            plan: The decompression plan to execute.
        """
        self.plan: DecompressionPlan = plan

    @classmethod
    def from_file(cls, src: str | Path, dest: str | Path | None = None) -> Self:
        """Create a DecompressionJob from file paths.

        Args:
            src: Source file path.
            dest: Destination file path; if None, derived from `src` as
                `plan_decompression` does.

        Returns:
            DecompressionJob: The created decompression job.
        """
        return cls(plan=plan_decompression(src, dest))

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Run the decompression job.

        Args:
            progress: Optional progress callback, invoked with
                `(fraction, done_bytes, total_bytes)`; counts compressed
                bytes consumed, so the total is the size of the source file
                rather than the size it expands to.
            cancel: Optional `CancelToken`.

        Returns:
            JobResult: The result of the decompression job.
        """
        if not self.plan.can_run:
            return JobResult(
                ok=False,
                error=RuntimeError(
                    self.plan.reason_if_unavailable or "Cannot decompress"
                ),
                plan=self.plan,
            )

        insp: InspectResult = self.plan.inspection

        total = insp.orig_size or 0
        try:
            if self.plan.format:
                # The format came from the file's magic bytes, so pass it on
                # rather than letting the C layer re-guess from the extension
                decompress_standalone(
                    input_path=str(object=self.plan.src),
                    output_path=str(object=self.plan.dest),
                    format=self.plan.format,
                    progress=to_core_progress(progress, total),
                    cancel=cancel,
                )

            else:
                decompress_file(
                    src_path=str(object=self.plan.src),
                    dst_path=str(object=self.plan.dest),
                    algo="",
                    progress=to_core_progress(progress, total),
                    cancel=cancel,
                )

            return JobResult(
                ok=True,
                error=None,
                plan=self.plan,
            )

        # Cancellation is a deliberate stop, so it is reported through its own
        # flag with no error attached
        except Cancelled:
            return JobResult(
                ok=False,
                error=None,
                plan=self.plan,
                cancelled=True,
            )

        # `run` reports failure through JobResult rather than raising
        except Exception as e:  # noqa: BLE001
            return JobResult(
                ok=False,
                error=e,
                plan=self.plan,
            )

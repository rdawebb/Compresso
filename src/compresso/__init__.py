"""Initialise the compressor package."""

from ._core import (
    BackendError,
    Cancelled,
    CancelToken,
    Error,
    HeaderError,
    compress_file,
    decompress_file,
)
from .frontend._job import JobHandle, JobResult, ProgressCallback
from .frontend.api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
    plan_compression,
    plan_decompression,
)
from .frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractOptions,
    ExtractPlan,
    OverwriteMode,
    plan_archive,
    plan_extraction,
)
from .introspect.benchmark import benchmark_file, print_results
from .introspect.capabilities import list_capabilities
from .introspect.file_inspect import InspectResult, inspect
from .introspect.speeds import get_estimated_speeds

__all__: list[str] = [
    "ArchiveEntry",
    "ArchiveJob",
    "ArchiveOptions",
    "ArchivePlan",
    "BackendError",
    "CancelToken",
    "Cancelled",
    "CompressionJob",
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionJob",
    "DecompressionPlan",
    "Error",
    "ExtractJob",
    "ExtractOptions",
    "ExtractPlan",
    "HeaderError",
    "InspectResult",
    "JobHandle",
    "JobResult",
    "OverwriteMode",
    "ProgressCallback",
    "benchmark_file",
    "compress_file",
    "decompress_file",
    "get_estimated_speeds",
    "inspect",
    "list_capabilities",
    "plan_archive",
    "plan_compression",
    "plan_decompression",
    "plan_extraction",
    "print_results",
]

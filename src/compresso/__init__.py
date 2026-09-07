"""Initialise the compressor package."""

from ._core import BackendError, Error, HeaderError, compress_file, decompress_file
from .backend.benchmark import benchmark_file, print_results
from .backend.capabilities import list_capabilities
from .backend.file_inspect import InspectResult, inspect
from .backend.speeds import get_estimated_speeds
from .frontend.api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
)
from .frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractPlan,
)

__all__: list[str] = [
    "ArchiveEntry",
    "ArchiveJob",
    "ArchiveOptions",
    "ArchivePlan",
    "BackendError",
    "CompressionJob",
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionJob",
    "DecompressionPlan",
    "Error",
    "ExtractJob",
    "ExtractPlan",
    "HeaderError",
    "InspectResult",
    "benchmark_file",
    "compress_file",
    "decompress_file",
    "get_estimated_speeds",
    "inspect",
    "list_capabilities",
    "print_results",
]

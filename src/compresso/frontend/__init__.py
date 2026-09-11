"""Frontend API for Compresso library."""

from .._core import Cancelled, CancelToken
from ._job import JobResult, ProgressCallback
from .api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
    plan_compression,
    plan_decompression,
)
from .archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractOptions,
    ExtractPlan,
    plan_archive,
    plan_extraction,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveJob",
    "ArchiveOptions",
    "ArchivePlan",
    "CancelToken",
    "Cancelled",
    "CompressionJob",
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionJob",
    "DecompressionPlan",
    "ExtractJob",
    "ExtractOptions",
    "ExtractPlan",
    "JobResult",
    "ProgressCallback",
    "plan_archive",
    "plan_compression",
    "plan_decompression",
    "plan_extraction",
]

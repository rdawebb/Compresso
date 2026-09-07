"""Frontend API for Compresso library."""

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
    ExtractPlan,
    plan_archive,
    plan_extraction,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveJob",
    "ArchiveOptions",
    "ArchivePlan",
    "CompressionJob",
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionJob",
    "DecompressionPlan",
    "ExtractJob",
    "ExtractPlan",
    "JobResult",
    "ProgressCallback",
    "plan_archive",
    "plan_compression",
    "plan_decompression",
    "plan_extraction",
]

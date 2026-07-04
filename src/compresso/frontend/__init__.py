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
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionPlan",
    "CompressionJob",
    "DecompressionJob",
    "plan_compression",
    "plan_decompression",
    "JobResult",
    "ProgressCallback",
    "ArchiveOptions",
    "ArchivePlan",
    "ExtractPlan",
    "ArchiveEntry",
    "ArchiveJob",
    "ExtractJob",
    "plan_archive",
    "plan_extraction",
]

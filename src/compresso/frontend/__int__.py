"""Frontend API for Compresso library."""

from .api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
)

__all__ = [
    "CompressionOptions",
    "CompressionPlan",
    "DecompressionPlan",
    "CompressionJob",
    "DecompressionJob",
]

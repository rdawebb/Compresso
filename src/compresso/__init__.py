"""Initialise the compressor package."""

from ._core import compress_file as _c_compress_file
from ._core import decompress_file as _c_decompress_file
from .backend.benchmark import benchmark_file, print_results
from .backend.capabilities import list_capabilities
from .backend.file_inspect import inspect, InspectResult

__all__ = [
    "benchmark_file",
    "print_results",
    "list_capabilities",
    "inspect",
    "InspectResult",
]


def compress_file(src_path, dest_path, *, algo=None, strategy="balanced", level=None):
    """Compress a file using the specified algorithm and strategy."""
    lvl = -1 if level is None else int(level)
    return _c_compress_file(src_path, dest_path, algo or "", strategy or "", lvl)


def decompress_file(src_path, dest_path, *, algo=None):
    """Decompress a file using the specified algorithm."""
    return _c_decompress_file(src_path, dest_path, algo or "")

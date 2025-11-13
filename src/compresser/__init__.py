"""Initialize the compressor package."""

from ._core import compress_file as _c_compress_file
from ._core import decompress_file as _c_decompress_file

def compress_file(src_path, dest_path, *, algo=None, strategy="balanced", level=None):
    """Compress a file using the specified algorithm and strategy."""
    lvl = -1 if level is None else int(level)
    return _c_compress_file(src_path, dest_path, algo or "", strategy or "", lvl)

def decompress_file(src_path, dest_path, *, algo=None):
    """Decompress a file using the specified algorithm."""
    return _c_decompress_file(src_path, dest_path, algo or "")
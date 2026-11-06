"""Advisory helpers about backends and files: capabilities, header inspection,
speed estimates and benchmarks.

The compression backends themselves are the C extension's, under csrc/.
"""

from .benchmark import benchmark_file
from .capabilities import list_capabilities
from .file_inspect import InspectResult, inspect
from .speeds import get_estimated_speeds

__all__: list[str] = [
    "InspectResult",
    "benchmark_file",
    "get_estimated_speeds",
    "inspect",
    "list_capabilities",
]

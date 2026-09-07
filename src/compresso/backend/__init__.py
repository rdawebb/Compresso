"""Compresso Backend API"""

from .benchmark import benchmark_file, print_results
from .capabilities import list_capabilities
from .file_inspect import InspectResult, inspect
from .speeds import get_estimated_speeds

__all__: list[str] = [
    "InspectResult",
    "benchmark_file",
    "get_estimated_speeds",
    "inspect",
    "list_capabilities",
    "print_results",
]

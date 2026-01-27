"""Pytest configuration and shared fixtures for Compresso tests."""

import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Ensure the src directory is in the path for imports
# This helps when the package isn't installed in development mode
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files.

    Yields:
        Path to the temporary directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_text_file(temp_dir: Path) -> Path:
    """Create a sample text file for testing.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        Path to the sample text file.
    """
    file_path = temp_dir / "sample.txt"
    content = "Hello, World! " * 100
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def sample_binary_file(temp_dir: Path) -> Path:
    """Create a sample binary file for testing.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        Path to the sample binary file.
    """
    file_path = temp_dir / "sample.bin"
    content = bytes(range(256)) * 100
    file_path.write_bytes(content)
    return file_path


@pytest.fixture
def large_compressible_file(temp_dir: Path) -> Path:
    """Create a large file with highly compressible content.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        Path to the large compressible file.
    """
    file_path = temp_dir / "large_compressible.txt"
    content = "A" * 1024 * 1024  # 1MB of repeated 'A'
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def empty_file(temp_dir: Path) -> Path:
    """Create an empty file for edge case testing.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        Path to the empty file.
    """
    file_path = temp_dir / "empty.txt"
    file_path.touch()
    return file_path


@pytest.fixture
def small_file(temp_dir: Path) -> Path:
    """Create a very small file for testing.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        Path to the small file.
    """
    file_path = temp_dir / "small.txt"
    file_path.write_text("Hi!", encoding="utf-8")
    return file_path


@pytest.fixture(params=["zlib", "bzip2", "lzma", "zstd", "lz4", "snappy"])
def compression_algo(request) -> str:
    """Parameterized fixture for all compression algorithms.

    Args:
        request: Pytest request object.

    Returns:
        Name of the compression algorithm.
    """
    return request.param


@pytest.fixture(params=["fast", "balanced", "max_ratio"])
def compression_strategy(request) -> str:
    """Parameterized fixture for all compression strategies.

    Args:
        request: Pytest request object.

    Returns:
        Name of the compression strategy.
    """
    return request.param


@pytest.fixture(params=[1, 3, 6, 9])
def compression_level(request) -> int:
    """Parameterized fixture for various compression levels.

    Args:
        request: Pytest request object.

    Returns:
        Compression level (1-9).
    """
    return request.param


@pytest.fixture
def mock_speeds_file(temp_dir: Path, monkeypatch) -> Path:
    """Mock the speeds file location for testing.

    Args:
        temp_dir: Temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Path to the mock speeds file.
    """
    speeds_file = temp_dir / "speeds.json"
    config_dir = temp_dir / ".compresso"
    config_dir.mkdir(exist_ok=True)

    # Patch the module-level variables
    from compresso.backend import speeds

    monkeypatch.setattr(speeds, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(speeds, "_SPEEDS_FILE", config_dir / "speeds.json")

    return speeds_file

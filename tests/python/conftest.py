"""Pytest configuration and shared fixtures for Compresso tests."""

from pathlib import Path

import pytest

from compresso.introspect import speeds

from .helpers import ARCHIVE_FORMATS


@pytest.fixture(autouse=True)
def speeds_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the speed estimates cache away from the user's home directory.

    Without this, anything that plans a job reads the real
    `~/.compresso/speeds.json`, so results would depend on the machine.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Path to the (not yet created) speeds file.
    """
    config_dir = tmp_path_factory.mktemp("config") / ".compresso"
    monkeypatch.setattr(speeds, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(speeds, "_SPEEDS_FILE", config_dir / "speeds.json")

    return config_dir / "speeds.json"


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """A temporary directory for test files.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the temporary directory.
    """
    return tmp_path


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
def compression_algo(request: pytest.FixtureRequest) -> str:
    """Parameterized fixture for all compression algorithms.

    Args:
        request: Pytest request object.

    Returns:
        Name of the compression algorithm.
    """
    return request.param


@pytest.fixture(params=ARCHIVE_FORMATS, ids=[f for f, _ in ARCHIVE_FORMATS])
def archive_format(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Each archive format paired with its extension.

    Args:
        request: Pytest request object.

    Returns:
        The archive format and extension as a tuple.
    """
    return request.param

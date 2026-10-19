"""Pytest configuration and shared fixtures for Compresso tests."""

import importlib
from pathlib import Path

import pytest

from compresso.introspect import speeds

from .helpers import ARCHIVE_FORMATS


def pytest_configure(config: pytest.Config) -> None:
    """Build the editable extension once, before any xdist worker starts.

    Args:
        config: The pytest config object.
    """
    if not hasattr(config, "workerinput"):
        importlib.import_module("compresso")


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
def sample_text_file(tmp_path: Path) -> Path:
    """Create a sample text file for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the sample text file.
    """
    file_path = tmp_path / "sample.txt"
    content = "Hello, World! " * 100
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def sample_binary_file(tmp_path: Path) -> Path:
    """Create a sample binary file for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the sample binary file.
    """
    file_path = tmp_path / "sample.bin"
    content = bytes(range(256)) * 100
    file_path.write_bytes(content)
    return file_path


@pytest.fixture
def large_compressible_file(tmp_path: Path) -> Path:
    """Create a large file with highly compressible content.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the large compressible file.
    """
    file_path = tmp_path / "large_compressible.txt"
    content = "A" * 1024 * 1024  # 1MB of repeated 'A'
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    """Create an empty file for edge case testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the empty file.
    """
    file_path = tmp_path / "empty.txt"
    file_path.touch()
    return file_path


@pytest.fixture
def small_file(tmp_path: Path) -> Path:
    """Create a very small file for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the small file.
    """
    file_path = tmp_path / "small.txt"
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

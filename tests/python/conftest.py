"""Pytest configuration and shared fixtures for Compresso tests."""

import importlib
import os
from pathlib import Path

import pytest

from compresso._core import compress_file, create_archive, get_capabilities
from compresso.introspect import speeds

from .helpers import ENTRY_COUNT, ENTRY_SIZE, PROGRESS_FILE_SIZE


def pytest_configure(config: pytest.Config) -> None:
    """Build the editable extension once, before any xdist worker starts."""
    if not hasattr(config, "workerinput"):
        importlib.import_module("compresso")


@pytest.fixture(autouse=True)
def speeds_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the speeds file, not yet created, away from the user's home directory.

    Without this, anything that plans a job reads the real
    `~/.compresso/speeds.json`, so results would depend on the machine.
    """
    config_dir = tmp_path_factory.mktemp("config") / ".compresso"
    monkeypatch.setattr(speeds, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(speeds, "_SPEEDS_FILE", config_dir / "speeds.json")

    return config_dir / "speeds.json"


@pytest.fixture
def needs_symlinks(tmp_path: Path) -> None:
    """Skip unless this process may create symlinks, which Windows restricts."""
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to("x")

    except OSError:
        pytest.skip("symlinks need privilege")

    probe.unlink()


@pytest.fixture
def sample_text_file(tmp_path: Path) -> Path:
    """A small, compressible text file."""
    file_path = tmp_path / "sample.txt"
    content = "Hello, World! " * 100
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def sample_binary_file(tmp_path: Path) -> Path:
    """A binary file holding every byte value."""
    file_path = tmp_path / "sample.bin"
    content = bytes(range(256)) * 100
    file_path.write_bytes(content)
    return file_path


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    """An empty file."""
    file_path = tmp_path / "empty.txt"
    file_path.touch()
    return file_path


@pytest.fixture
def small_file(tmp_path: Path) -> Path:
    """A three-byte file."""
    file_path = tmp_path / "small.txt"
    file_path.write_text("Hi!", encoding="utf-8")
    return file_path


@pytest.fixture(params=[cap["name"] for cap in get_capabilities()])
def compression_algo(request: pytest.FixtureRequest) -> str:
    """Each compiled compression backend's name."""
    return request.param


@pytest.fixture(scope="module")
def big_compressible_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A file large enough to produce several progress reports when compressed.

    Shared by the whole module, so tests must only read it.
    """
    file_path = tmp_path_factory.mktemp("progress") / "big_compressible.bin"
    chunk = b"compresso progress test payload " * 1024  # 32 KB
    file_path.write_bytes(chunk * (PROGRESS_FILE_SIZE // len(chunk)))

    return file_path


@pytest.fixture(scope="module")
def big_incompressible_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A file that stays large once compressed.

    Progress counts input bytes, so decompression's total is the compressed
    size; random data keeps that above the report-interval floor; shared by
    the whole module, so tests must only read it.
    """
    file_path = tmp_path_factory.mktemp("progress") / "big_incompressible.bin"
    file_path.write_bytes(os.urandom(PROGRESS_FILE_SIZE))

    return file_path


@pytest.fixture(scope="module")
def compressed_file(
    tmp_path_factory: pytest.TempPathFactory, big_incompressible_file: Path
) -> Path:
    """`big_incompressible_file` compressed to a .comp, shared by the whole module."""
    path = tmp_path_factory.mktemp("progress") / "big.comp"
    compress_file(big_incompressible_file, path, "zstd", "balanced", 1)

    return path


@pytest.fixture(scope="module")
def source_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory of files large enough to produce several progress reports.

    Shared by the whole module, so tests must only read it.
    """
    root = tmp_path_factory.mktemp("progress") / "src"
    root.mkdir()
    for i in range(ENTRY_COUNT):
        (root / f"f{i}.bin").write_bytes(os.urandom(ENTRY_SIZE))

    return root


@pytest.fixture(scope="module")
def source_tar(tmp_path_factory: pytest.TempPathFactory, source_tree: Path) -> Path:
    """`source_tree` as a plain tar, shared by the whole module."""
    path = tmp_path_factory.mktemp("progress") / "src.tar"
    create_archive(path, "tar", [source_tree])

    return path

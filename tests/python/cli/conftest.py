"""Shared helpers and fixtures for the CLI tests."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

import compresso.cli._run as run_mod
from compresso.cli import app
from compresso.cli._render import (
    BAR_THRESHOLD,
    EXIT_FAILED,
    EXIT_OK,
    progress_bar,
)
from compresso.frontend._job import ProgressCallback
from compresso.frontend.archive_api import (
    ArchiveJob,
    ArchiveOptions,
)

runner = CliRunner()

# Incompressible throughout, so compressed sizes stay close to these
PAYLOAD_SIZE = 64 * 1024
TREE_FILE_SIZE = 16 * 1024

# Past the progress bar threshold, for the tests that cover the bar
LARGE_PAYLOAD_SIZE = 2 * BAR_THRESHOLD


def invoke(*args: str | Path, expect: int | None = EXIT_OK) -> Result:
    """Run the CLI, asserting it exits with `expect`.

    Args:
        *args: The command line, without the program name.
        expect: The exit code to assert, or None to leave it unchecked.

    Returns:
        The runner's result.
    """
    result = runner.invoke(app, [str(arg) for arg in args])
    if expect is not None:
        assert result.exit_code == expect, result.output

    return result


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    """A small incompressible file, below the progress bar threshold."""
    path = tmp_path / "payload.bin"
    path.write_bytes(os.urandom(PAYLOAD_SIZE))

    return path


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """A small directory tree to archive, function-scoped because some tests delete it."""
    root = tmp_path / "tree"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.bin").write_bytes(os.urandom(TREE_FILE_SIZE))

    return root


@pytest.fixture
def make_archive(source_tree: Path, tmp_path: Path) -> Callable[[str], Path]:
    """Build an archive of `source_tree` as input for a test.

    Built through the API rather than the CLI, and checked, so a broken setup
    cannot surface as a confusing failure later in the test.
    """

    def make(fmt: str) -> Path:
        archive = tmp_path / f"out.{fmt}"
        options = ArchiveOptions(format=fmt)
        result = ArchiveJob.from_paths([source_tree], archive, options).run()
        assert result.ok, result.error
        return archive

    return make


@dataclass
class CompressTarget:
    """What one `compress` call reads and writes."""

    source: Path
    dest: Path
    args: tuple[str, ...]


@pytest.fixture(params=["file", "tree"])
def compress_target(
    request: pytest.FixtureRequest, payload: Path, source_tree: Path, tmp_path: Path
) -> CompressTarget:
    """A single file into a .comp container, or a tree into a tar archive.

    The two take different paths through `compress`, but share its
    overwrite-mode handling.
    """
    if request.param == "file":
        return CompressTarget(payload, tmp_path / "out.comp", ())

    return CompressTarget(source_tree, tmp_path / "out.tar", ("-f", "tar"))


@dataclass
class Bar:
    """One progress bar a command opened."""

    enabled: bool
    updates: int = 0


@pytest.fixture
def bars(monkeypatch: pytest.MonkeyPatch) -> list[Bar]:
    """Record each progress bar a command opens, in order, still drawing the real one."""
    opened: list[Bar] = []

    @contextmanager
    def spy(
        label: str, total: int, *, enabled: bool
    ) -> Generator[ProgressCallback | None]:
        bar = Bar(enabled)
        opened.append(bar)
        with progress_bar(label, total, enabled=enabled) as on_progress:
            if on_progress is None:
                yield None
                return

            def counting(fraction: float, done: int, reported_total: int) -> None:
                bar.updates += 1
                on_progress(fraction, done, reported_total)

            yield counting

    monkeypatch.setattr(run_mod, "progress_bar", spy)

    return opened


# Each overwrite mode against an existing output: whether the old output
# survives, and whether the new one is written beside it under a new name
CONFLICT_MODES = pytest.mark.parametrize(
    "flags,expect,kept,renames",
    [
        ((), EXIT_OK, True, True),
        (("--error-on-conflict",), EXIT_FAILED, True, False),
        (("--skip-existing",), EXIT_OK, True, False),
        (("--overwrite",), EXIT_OK, False, False),
    ],
    ids=["rename", "error", "skip", "overwrite"],
)

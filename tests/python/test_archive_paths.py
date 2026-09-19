"""Tests for the entry names stored when creating an archive.

Stored names keep only the source's own final component, so an archive is
extractable however its sources were spelled on the command line.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from compresso._core import (
    create_archive,
    extract_archive,
    list_archive_contents,
)

ARCHIVE_FORMATS = [("tar", ".tar"), ("tar.zst", ".tar.zst"), ("zip", ".zip")]

PAYLOAD = b"compresso entry name test\n"


@pytest.fixture(params=ARCHIVE_FORMATS, ids=[f for f, _ in ARCHIVE_FORMATS])
def archive_format(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Each archive format paired with its extension.

    Args:
        request: The pytest request object.

    Returns:
        The archive format and extension as a tuple.
    """
    return request.param


def stored_names(archive: Path) -> set[str]:
    """The archive's entry names, without the trailing separator ZIP adds.

    Args:
        archive: Path to the archive to read.

    Returns:
        The set of stored entry names.
    """
    return {entry["path"].rstrip("/") for entry in list_archive_contents(str(archive))}


class TestStoredEntryNames:
    """Tests the names written for each shape of source path."""

    def test_absolute_file_round_trips(
        self, temp_dir: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a single file given by absolute path can be extracted."""
        fmt, ext = archive_format
        source = temp_dir / "payload.txt"
        source.write_bytes(PAYLOAD)
        archive = temp_dir / f"out{ext}"

        create_archive(str(archive), fmt, [str(source)], 3)

        assert stored_names(archive) == {"payload.txt"}

        dest = temp_dir / "out"
        extract_archive(str(archive), str(dest), [])

        assert (dest / "payload.txt").read_bytes() == PAYLOAD

    def test_directory_with_trailing_separator_round_trips(
        self, temp_dir: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a trailing separator on a source directory is ignored."""
        fmt, ext = archive_format
        tree = temp_dir / "tree"
        (tree / "nested").mkdir(parents=True)
        (tree / "nested" / "payload.txt").write_bytes(PAYLOAD)
        archive = temp_dir / f"out{ext}"

        create_archive(str(archive), fmt, [str(tree) + os.sep], 3)

        assert stored_names(archive) == {
            "tree",
            "tree/nested",
            "tree/nested/payload.txt",
        }

        dest = temp_dir / "out"
        extract_archive(str(archive), str(dest), [])

        assert (dest / "tree" / "nested" / "payload.txt").read_bytes() == PAYLOAD

    def test_parent_relative_file_round_trips(
        self,
        temp_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a source reached through ".." stores just its name."""
        fmt, ext = archive_format
        source = temp_dir / "payload.txt"
        source.write_bytes(PAYLOAD)
        workdir = temp_dir / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        create_archive(f"out{ext}", fmt, [os.path.join("..", "payload.txt")], 3)

        archive = workdir / f"out{ext}"
        assert stored_names(archive) == {"payload.txt"}

        extract_archive(str(archive), "out", [])

        assert (workdir / "out" / "payload.txt").read_bytes() == PAYLOAD

    def test_nested_relative_file_stores_only_its_name(
        self,
        temp_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a file below the working directory drops its parents."""
        fmt, ext = archive_format
        nested = temp_dir / "sub" / "deeper"
        nested.mkdir(parents=True)
        (nested / "payload.txt").write_bytes(PAYLOAD)
        monkeypatch.chdir(temp_dir)

        create_archive(
            f"out{ext}", fmt, [os.path.join("sub", "deeper", "payload.txt")], 3
        )

        assert stored_names(temp_dir / f"out{ext}") == {"payload.txt"}

    def test_source_without_a_usable_name_is_refused(
        self,
        temp_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a source named ".." fails when writing, not when extracting."""
        fmt, ext = archive_format
        workdir = temp_dir / "tree" / "sub"
        workdir.mkdir(parents=True)
        monkeypatch.chdir(workdir)

        with pytest.raises(ValueError, match="unsafe entry name"):
            create_archive(str(temp_dir / f"out{ext}"), fmt, [".."], 3)

    def test_sources_of_both_kinds_agree(
        self, temp_dir: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a file and a directory given the same way are stored alike."""
        fmt, ext = archive_format
        source = temp_dir / "payload.txt"
        source.write_bytes(PAYLOAD)
        tree = temp_dir / "tree"
        tree.mkdir()
        (tree / "inner.txt").write_bytes(PAYLOAD)
        archive = temp_dir / f"out{ext}"

        create_archive(str(archive), fmt, [str(source), str(tree)], 3)

        assert stored_names(archive) == {"payload.txt", "tree", "tree/inner.txt"}

        dest = temp_dir / "out"
        extract_archive(str(archive), str(dest), [])

        assert (dest / "payload.txt").read_bytes() == PAYLOAD
        assert (dest / "tree" / "inner.txt").read_bytes() == PAYLOAD

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

PAYLOAD = b"compresso entry name test\n"

pytestmark = pytest.mark.parametrize(
    "archive_format",
    [("tar", ".tar"), ("tar.zst", ".tar.zst"), ("zip", ".zip")],
    ids=["tar", "tar.zst", "zip"],
)


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

    def test_directory_with_trailing_separator_round_trips(
        self, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a trailing separator on a source directory is ignored."""
        fmt, ext = archive_format
        tree = tmp_path / "tree"
        (tree / "nested").mkdir(parents=True)
        (tree / "nested" / "payload.txt").write_bytes(PAYLOAD)
        archive = tmp_path / f"out{ext}"

        create_archive(str(archive), fmt, [str(tree) + os.sep])

        assert stored_names(archive) == {
            "tree",
            "tree/nested",
            "tree/nested/payload.txt",
        }

        dest = tmp_path / "out"
        extract_archive(str(archive), str(dest), [])

        assert (dest / "tree" / "nested" / "payload.txt").read_bytes() == PAYLOAD

    def test_parent_relative_file_round_trips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a source reached through ".." stores just its name."""
        fmt, ext = archive_format
        source = tmp_path / "payload.txt"
        source.write_bytes(PAYLOAD)
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        create_archive(f"out{ext}", fmt, [os.path.join("..", "payload.txt")])

        archive = workdir / f"out{ext}"
        assert stored_names(archive) == {"payload.txt"}

        extract_archive(str(archive), "out", [])

        assert (workdir / "out" / "payload.txt").read_bytes() == PAYLOAD

    def test_nested_relative_file_stores_only_its_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a file below the working directory drops its parents."""
        fmt, ext = archive_format
        nested = tmp_path / "sub" / "deeper"
        nested.mkdir(parents=True)
        (nested / "payload.txt").write_bytes(PAYLOAD)
        monkeypatch.chdir(tmp_path)

        create_archive(f"out{ext}", fmt, [os.path.join("sub", "deeper", "payload.txt")])

        assert stored_names(tmp_path / f"out{ext}") == {"payload.txt"}

    def test_source_without_a_usable_name_is_refused(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        archive_format: tuple[str, str],
    ) -> None:
        """Test that a source named ".." fails when writing, not when extracting."""
        fmt, ext = archive_format
        workdir = tmp_path / "tree" / "sub"
        workdir.mkdir(parents=True)
        monkeypatch.chdir(workdir)

        with pytest.raises(ValueError, match="unsafe entry name"):
            create_archive(str(tmp_path / f"out{ext}"), fmt, [".."])

    def test_sources_of_both_kinds_agree(
        self, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a file and a directory given by absolute path are stored alike."""
        fmt, ext = archive_format
        source = tmp_path / "payload.txt"
        source.write_bytes(PAYLOAD)
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "inner.txt").write_bytes(PAYLOAD)
        archive = tmp_path / f"out{ext}"

        create_archive(str(archive), fmt, [str(source), str(tree)])

        assert stored_names(archive) == {"payload.txt", "tree", "tree/inner.txt"}

        dest = tmp_path / "out"
        extract_archive(str(archive), str(dest), [])

        assert (dest / "payload.txt").read_bytes() == PAYLOAD
        assert (dest / "tree" / "inner.txt").read_bytes() == PAYLOAD

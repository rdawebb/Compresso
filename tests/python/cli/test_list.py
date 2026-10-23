"""Tests for `compresso list`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import orjson
import pytest

from compresso.cli._render import EXIT_FAILED, format_size
from compresso.cli._render.info import list_entries
from compresso.frontend.archive_api import ArchiveEntry

from .conftest import TREE_FILE_SIZE, invoke


class TestList:
    """Test the read-only `list` command."""

    def test_list_shows_sizes_and_paths(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that list prints each entry's size and path."""
        lines = invoke("list", make_archive("tar")).output.splitlines()

        f0_line = next(line for line in lines if "f0.bin" in line)
        assert f0_line.split()[:2] == format_size(TREE_FILE_SIZE).split()

        # Directories have a blank size; their contents are indented beneath them
        dir_line = next(line for line in lines if line.rstrip("/").endswith("tree"))
        assert dir_line == f"{'':>10}  {dir_line.strip()}"
        assert f0_line.endswith("    tree/f0.bin")

    def test_list_indents_by_depth(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that list indents only under directories it has listed."""
        entries = [
            ArchiveEntry(path="a/", is_dir=True),
            ArchiveEntry(path="a/b/", is_dir=True),
            ArchiveEntry(path="a/b/c.txt", size=1024),
            ArchiveEntry(path="a/d.txt", size=10),
            ArchiveEntry(path="a/e", size=5, is_symlink=True, link_target="d.txt"),
            ArchiveEntry(path="x/y.txt", size=0),  # no "x/" entry was listed
        ]

        list_entries(entries)

        assert capsys.readouterr().out.splitlines() == [
            " " * 12 + "a/",
            " " * 14 + "a/b/",
            "   1.00 KB" + " " * 6 + "a/b/c.txt",
            "   10.00 B" + " " * 4 + "a/d.txt",
            " " * 14 + "a/e -> d.txt",
            "    0.00 B" + " " * 2 + "x/y.txt",
        ]

    def test_long_shows_mode_and_zip_detail(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that -l shows each entry's mode, and a zip entry's CRC."""
        lines = invoke("list", "-l", make_archive("zip")).output.splitlines()

        f0_line = next(line for line in lines if "f0.bin" in line)
        assert f0_line.startswith("-rw")
        assert ", crc " in f0_line

    def test_several_inputs_carry_on_past_a_single_file(
        self, payload: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that each archive gets a header, and a single-file input fails
        alone, exiting 1 once both archives are listed."""
        tar, zip_ = make_archive("tar"), make_archive("zip")
        packed = tmp_path / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        result = invoke("list", tar, packed, zip_, expect=EXIT_FAILED)

        assert f"{packed}: holds a single file" in result.output
        assert result.stdout.startswith(f"==> {tar} <==\n")
        assert f"\n\n==> {zip_} <==\n" in result.stdout
        assert f"==> {packed} <==" not in result.stdout

    def test_json_is_an_array_per_archive(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --json is an array even for one input, with every entry field."""
        archive = make_archive("zip")

        (listing,) = orjson.loads(invoke("list", archive, "--json").output)

        assert listing["path"] == str(archive)
        assert listing["format"] == "zip"
        f0 = next(e for e in listing["entries"] if e["path"].endswith("f0.bin"))
        assert f0["size"] == TREE_FILE_SIZE
        assert f0["crc"] is not None

"""Tests for `compresso extract`."""

from __future__ import annotations

import gzip
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

from compresso.cli._render import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    format_size,
)
from compresso.cli._render.info import list_entries
from compresso.frontend.archive_api import (
    ArchiveEntry,
)

from ..helpers import file_entry, link_entry, make_tar, renamed, umask
from .conftest import CONFLICT_MODES, TREE_FILE_SIZE, invoke


class TestArchiveRoundTrip:
    """Test archive and extract, end to end."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "tar.zst", "zip"])
    def test_archive_then_extract(
        self, source_tree: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a tree survives a round trip through each archive format."""
        archive = tmp_path / f"out.{fmt}"
        dest = tmp_path / f"dest.{fmt}"

        result = invoke("compress", source_tree, "-o", archive, "-f", fmt)
        assert "Archive created" in result.output

        result = invoke("extract", archive, "-o", dest)
        assert "Extraction successful" in result.output

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_extract_list_only(
        self,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that --list prints each entry's size and path without writing anything."""
        archive = make_archive("tar")
        dest = tmp_path / "dest"

        result = invoke("extract", archive, "--list", "-o", dest)

        lines = result.output.splitlines()
        f0_line = next(line for line in lines if "f0.bin" in line)
        assert f0_line.split()[:2] == format_size(TREE_FILE_SIZE).split()
        assert not dest.exists()

        # Directories have a blank size; their contents are indented beneath them
        dir_line = next(line for line in lines if line.rstrip("/").endswith("tree"))
        assert dir_line == f"{'':>10}  {dir_line.strip()}"
        assert f0_line.endswith("    tree/f0.bin")

    def test_extract_list_indents_by_depth(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that --list indents only under directories it has listed."""
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

    @pytest.mark.parametrize(
        "flags,expect,renames",
        [
            ((), EXIT_OK, True),
            (("--error-on-conflict",), EXIT_FAILED, False),
            (("--skip-existing",), EXIT_OK, False),
            (("--overwrite",), EXIT_OK, False),
        ],
        ids=["rename", "error", "skip", "overwrite"],
    )
    def test_extract_over_an_existing_tree(
        self,
        source_tree: Path,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        flags: tuple[str, ...],
        expect: int,
        renames: bool,
    ) -> None:
        """Test that a repeat extraction renames the clashing top-level directory
        by default, and follows the overwrite mode otherwise."""
        archive = make_archive("tar")
        dest = tmp_path / "dest"
        invoke("extract", archive, "-o", dest, "-q")

        invoke("extract", archive, "-o", dest, *flags, "-q", expect=expect)

        top_level = [source_tree.name, *([renamed(source_tree.name)] * renames)]
        assert sorted(p.name for p in dest.iterdir()) == sorted(top_level)
        # The whole directory is renamed, never a file inside it
        names = sorted(p.name for p in source_tree.iterdir())
        for top in top_level:
            assert sorted(p.name for p in (dest / top).iterdir()) == names

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    @pytest.mark.parametrize("exact", [False, True], ids=["umask", "same-perms"])
    def test_same_permissions_skips_the_umask(
        self, tmp_path: Path, exact: bool
    ) -> None:
        """Test that -p restores an entry's mode without the umask applied."""
        archive = tmp_path / "modes.tar"
        make_tar(archive, file_entry("a.txt", mode=0o777))
        dest = tmp_path / "dest"

        invoke("extract", archive, "-o", dest, *(["-p"] if exact else []), "-q")

        expected = 0o777 if exact else 0o777 & ~umask()
        assert (dest / "a.txt").stat().st_mode & 0o7777 == expected

    def test_symlinks_flag_reaches_the_policy(self, tmp_path: Path) -> None:
        """Test that a symlink refused by default extracts with `--symlinks file`."""
        archive = tmp_path / "link.tar"
        make_tar(archive, link_entry("link", "target", kind=tarfile.SYMTYPE))
        dest = tmp_path / "dest"

        invoke("extract", archive, "-o", dest, "-q", expect=EXIT_FAILED)
        invoke("extract", archive, "-o", dest, "--symlinks", "file", "-q")

        assert (dest / "link").read_bytes() == b"target"


class TestDecompressConflicts:
    """Test what decompressing one file does when its output already exists."""

    @CONFLICT_MODES
    def test_existing_output(
        self,
        payload: Path,
        tmp_path: Path,
        flags: tuple[str, ...],
        expect: int,
        kept: bool,
        renames: bool,
    ) -> None:
        """Test that each overwrite mode keeps, replaces or renames around the output."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"
        invoke("compress", payload, "-o", archive, "-q")
        restored.write_bytes(b"stale")

        result = invoke("decompress", archive, "-o", restored, *flags, expect=expect)

        sibling = tmp_path / renamed(restored.name)
        assert (f"Renamed to:        {sibling}" in result.output) == renames
        assert restored.read_bytes() == (b"stale" if kept else payload.read_bytes())
        assert sibling.exists() == renames


class TestDecompressName:
    """Test that -N names a .gz file's output as its header does, as gzip -N does."""

    @pytest.mark.parametrize(
        ("flags", "expected"), [((), "renamed"), (("-N",), "orig.txt")]
    )
    def test_name_flag(
        self, tmp_path: Path, flags: tuple[str, ...], expected: str
    ) -> None:
        """Test that the stored name is used only with -N."""
        src = tmp_path / "renamed.gz"
        with src.open("wb") as f, gzip.GzipFile("orig.txt", "wb", fileobj=f) as gz:
            gz.write(b"payload")

        invoke("extract", src, *flags, "-q")

        assert (tmp_path / expected).read_bytes() == b"payload"


class TestSmartExtract:
    """Test `extract` runs the jobs dispatch plans; the rules are in frontend/test_dispatch.py."""

    def test_several_inputs_in_one_go(self, payload: Path, tmp_path: Path) -> None:
        """Test that each input is dispatched on its own."""
        first = tmp_path / "a.gz"
        second = tmp_path / "b.zst"
        invoke("compress", payload, "-o", first, "-q")
        invoke("compress", payload, "-o", second, "-q")

        invoke("extract", first, second, "-q")

        assert (tmp_path / "a").read_bytes() == payload.read_bytes()
        assert (tmp_path / "b").read_bytes() == payload.read_bytes()

    def test_listing_a_single_file_container_is_refused(
        self, payload: Path, tmp_path: Path
    ) -> None:
        """Test that there is nothing to list in a container holding one file."""
        packed = tmp_path / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        result = invoke("extract", packed, "--list", expect=EXIT_USAGE)

        assert "nothing to list" in result.output


class TestMergedVerbCompatibility:
    """Test that the old names still work, and the old argument order says so."""

    def test_decompress_now_handles_archives_too(
        self,
        source_tree: Path,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that the merged verb does not care which name was typed."""
        dest = tmp_path / "out"

        invoke("decompress", make_archive("tar"), "-o", dest, "-q")

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

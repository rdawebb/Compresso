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
)

from ..helpers import file_entry, link_entry, make_tar, renamed, umask
from .conftest import CONFLICT_MODES, invoke


class TestArchiveRoundTrip:
    """Test archive and extract, end to end."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "tar.zst", "zip"])
    def test_archive_then_extract(
        self, source_tree: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a tree survives a round trip through each archive format."""
        archive = tmp_path / f"out.{fmt}"
        dest = tmp_path / f"dest.{fmt}"

        result = invoke("compress", source_tree, "-o", archive, "-F", fmt)
        assert "Archive created" in result.output

        result = invoke("extract", archive, "-o", dest)
        assert "Extraction successful" in result.output

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    @pytest.mark.parametrize(
        "flags,expect,renames",
        [
            ((), EXIT_OK, True),
            (("--on-conflict", "error"), EXIT_FAILED, False),
            (("--on-conflict", "skip"), EXIT_OK, False),
            (("--on-conflict", "overwrite"), EXIT_OK, False),
            (("-f",), EXIT_OK, False),
        ],
        ids=["rename", "error", "skip", "overwrite", "force"],
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

        result = invoke("extract", archive, "-o", restored, *flags, expect=expect)

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

    # Unrecognised fails while planning, truncated while running; random bytes
    # named .gz would be tried as gzip, so the unrecognised one has no codec suffix
    @pytest.mark.parametrize("bad", ["unrecognised", "truncated"])
    def test_a_failed_input_does_not_stop_the_rest(
        self,
        payload: Path,
        source_tree: Path,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        bad: str,
    ) -> None:
        """Test that the inputs either side of a bad one are still extracted, and
        the bad one is named and exits 1."""
        good = tmp_path / "good.gz"
        good.write_bytes(gzip.compress(payload.read_bytes()))

        if bad == "unrecognised":
            broken = tmp_path / "bad.bin"
            broken.write_bytes(payload.read_bytes())
        else:
            broken = tmp_path / "bad.gz"
            broken.write_bytes(good.read_bytes()[: good.stat().st_size // 2])

        dest = tmp_path / "dest"
        dest.mkdir()

        result = invoke(
            "extract",
            good,
            broken,
            make_archive("zip"),
            "-o",
            dest,
            "-q",
            expect=EXIT_FAILED,
        )

        assert str(broken) in result.output
        assert (dest / "good").read_bytes() == payload.read_bytes()
        assert not (dest / "bad").exists()
        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

"""Tests for `compresso extract`."""

from __future__ import annotations

import gzip
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

import compresso.cli.extract as extract_cmd
from compresso.cli._render import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)
from compresso.frontend.dispatch import ExtractRequest

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

        invoke("compress", source_tree, "-o", archive, "-F", fmt, "-q")
        invoke("extract", archive, "-C", dest, "-q")

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

        invoke("extract", archive, "-C", dest, "-q")
        invoke("extract", archive, "-C", dest, *flags, "-q", expect=expect)

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

        invoke("extract", archive, "-C", dest, *(["-p"] if exact else []), "-q")

        expected = 0o777 if exact else 0o777 & ~umask()
        assert (dest / "a.txt").stat().st_mode & 0o7777 == expected

    def test_symlinks_flag_reaches_the_policy(self, tmp_path: Path) -> None:
        """Test that a symlink refused by default extracts with `--symlinks file`."""
        archive = tmp_path / "link.tar"
        make_tar(archive, link_entry("link", "target", kind=tarfile.SYMTYPE))
        dest = tmp_path / "dest"

        invoke("extract", archive, "-C", dest, "-q", expect=EXIT_FAILED)
        invoke("extract", archive, "-C", dest, "--symlinks", "file", "-q")

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
        """Test that each overwrite mode keeps, replaces or renames around the
        output, and the summary line says which."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"
        invoke("compress", payload, "-o", archive, "-q")
        restored.write_bytes(b"stale")

        result = invoke("extract", archive, "-o", restored, *flags, expect=expect)

        sibling = tmp_path / renamed(restored.name)
        assert (f"output renamed to {sibling}" in result.output) == renames
        assert (f"{restored}  skipped" in result.output) == ("skip" in flags)
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
            "-C",
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


class TestOutputFlags:
    """Test that -o names one file and -C the directory for everything else."""

    def test_output_with_directory(self, payload: Path, tmp_path: Path) -> None:
        """Test that -o contradicts -C."""
        packed = tmp_path / "a.gz"
        packed.write_bytes(gzip.compress(payload.read_bytes()))

        result = invoke(
            "extract", packed, "-o", tmp_path / "x", "-C", tmp_path, expect=EXIT_USAGE
        )
        assert "-o contradicts -C" in result.output

    def test_output_with_several_inputs(self, payload: Path, tmp_path: Path) -> None:
        """Test that -o refuses more than one input, pointing to -C."""
        inputs = [tmp_path / "a.gz", tmp_path / "b.gz"]
        for packed in inputs:
            packed.write_bytes(gzip.compress(payload.read_bytes()))

        result = invoke("extract", *inputs, "-o", tmp_path / "x", expect=EXIT_USAGE)
        assert "use -C" in result.output

    def test_output_on_an_archive(
        self, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that -o on an input that turns out to be an archive fails it."""
        archive = make_archive("zip")
        out = tmp_path / "out"

        result = invoke("extract", archive, "-o", out, expect=EXIT_FAILED)
        assert f"{archive}: -o names one file; use -C for an archive" in result.output
        assert not out.exists()


class TestEntry:
    """Test that --entry extracts only the entries named."""

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_only_the_chosen_entries(
        self,
        source_tree: Path,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        fmt: str,
    ) -> None:
        """Test that the named entries are written and nothing else."""
        archive = make_archive(fmt)
        dest = tmp_path / "dest"
        top = source_tree.name

        invoke(
            "extract",
            archive,
            "-C",
            dest,
            "--entry",
            f"{top}/f0.bin",
            "-e",
            f"{top}/f2.bin",
            "-q",
        )

        assert sorted(p.name for p in (dest / top).iterdir()) == ["f0.bin", "f2.bin"]
        for name in ("f0.bin", "f2.bin"):
            assert (dest / top / name).read_bytes() == (source_tree / name).read_bytes()

    def test_a_missing_entry_fails_after_the_rest(
        self, source_tree: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that a name matching nothing exits 1, naming it, once the rest is
        written."""
        dest = tmp_path / "dest"
        top = source_tree.name

        result = invoke(
            "extract",
            make_archive("tar"),
            "-C",
            dest,
            "--entry",
            f"{top}/f1.bin",
            "-e",
            "nope",
            expect=EXIT_FAILED,
        )

        assert "nope" in result.output
        assert sorted(p.name for p in (dest / top).iterdir()) == ["f1.bin"]

    def test_a_single_file_input_fails(self, payload: Path, tmp_path: Path) -> None:
        """Test that --entry on an input that isn't an archive fails it."""
        packed = tmp_path / "a.gz"
        packed.write_bytes(gzip.compress(payload.read_bytes()))

        result = invoke("extract", packed, "--entry", "x", expect=EXIT_FAILED)

        assert (
            f"{packed}: --entry, --include and --exclude apply only to archives"
            in result.output
        )
        assert not (tmp_path / "a").exists()


class TestFilter:
    """Test that --include and --exclude select entries."""

    def test_include_adds_to_entry_and_exclude_overrides(
        self, source_tree: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test bsdtar's union, with the exclude winning over both."""
        dest = tmp_path / "dest"
        top = source_tree.name

        invoke(
            "extract",
            make_archive("tar"),
            "-C",
            dest,
            "-e",
            f"{top}/f0.bin",
            "--include",
            "f[12].bin",
            "--exclude",
            "f2.bin",
            "-q",
        )

        assert sorted(p.name for p in (dest / top).iterdir()) == ["f0.bin", "f1.bin"]

    @pytest.mark.parametrize("flags", [(), ("-e", "tree/f0.bin")])
    def test_excluding_everything_warns_and_succeeds(
        self,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        flags: tuple[str, ...],
    ) -> None:
        """Test that a filter leaving nothing exits 0 as tar does, but says so."""
        archive = make_archive("tar")

        result = invoke(
            "extract", archive, "-C", tmp_path / "d", *flags, "--exclude", "*"
        )

        assert f"{archive}: nothing left to extract" in result.output
        assert not list((tmp_path / "d").iterdir())

    def test_quiet_silences_the_warning(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that -q suppresses even the warning."""
        result = invoke("extract", make_archive("tar"), "--exclude", "*", "-q")

        assert result.output == ""

    def test_a_partly_excluded_run_does_not_warn(
        self, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that the warning needs every entry gone."""
        result = invoke(
            "extract", make_archive("tar"), "-C", tmp_path / "d", "--exclude", "f0.bin"
        )

        assert "nothing left" not in result.output

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_a_single_file_input_fails(
        self, payload: Path, tmp_path: Path, flag: str
    ) -> None:
        """Test that a glob on an input that isn't an archive fails it."""
        packed = tmp_path / "a.gz"
        packed.write_bytes(gzip.compress(payload.read_bytes()))

        result = invoke("extract", packed, flag, "x", expect=EXIT_FAILED)

        assert "apply only to archives" in result.output
        assert not (tmp_path / "a").exists()


class TestExtractOptions:
    """Test the flags that set `ExtractOptions`, named as tar names them."""

    @pytest.mark.parametrize(
        "flags,field,value",
        [
            (("--no-same-permissions",), "preserve_permissions", False),
            (("-m",), "preserve_timestamps", False),
            (("--overwrite-dir",), "overwrite_dir_metadata", True),
            (("--max-depth", "5"), "max_depth", 5),
            (("--max-depth", "0"), "max_depth", 0),
            (("--max-size", "500M"), "max_total_size", 500 * 1024**2),
            (("--max-size", "10g"), "max_total_size", 10 * 1024**3),
            (("--max-size", "1KB"), "max_total_size", 1024),
            (("--max-size", "2048"), "max_total_size", 2048),
            (("--max-size", "0"), "max_total_size", 0),
        ],
    )
    def test_flag_reaches_its_field(
        self,
        payload: Path,
        monkeypatch: pytest.MonkeyPatch,
        flags: tuple[str, ...],
        field: str,
        value: object,
    ) -> None:
        """Test that each flag sets its field, recorded before anything runs."""
        seen: list[ExtractRequest] = []

        def record(request: ExtractRequest) -> None:
            seen.append(request)
            raise KeyboardInterrupt

        monkeypatch.setattr(extract_cmd, "plan_extract", record)

        invoke("extract", payload, *flags, expect=EXIT_CANCELLED)

        assert getattr(seen[0].options, field) == value

    @pytest.mark.parametrize("size", ["5X", "-1", "1.5G", "M", "10MBB"])
    def test_bad_max_size_is_usage(self, payload: Path, size: str) -> None:
        """Test that a size that doesn't parse is refused before any work."""
        invoke("extract", payload, "--max-size", size, expect=EXIT_USAGE)

    def test_same_permissions_contradicts_no_same_permissions(
        self, payload: Path
    ) -> None:
        """Test that -p with --no-same-permissions exits 2."""
        result = invoke(
            "extract", payload, "-p", "--no-same-permissions", expect=EXIT_USAGE
        )
        assert "-p contradicts --no-same-permissions" in result.output

    @pytest.mark.parametrize("touch", [False, True], ids=["mtime", "touch"])
    def test_touch_skips_the_stored_time(self, tmp_path: Path, touch: bool) -> None:
        """Test that -m leaves an entry with the time it was extracted."""
        archive = tmp_path / "old.tar"
        make_tar(archive, file_entry("a.txt", mtime=1_000_000))
        dest = tmp_path / "dest"

        invoke("extract", archive, "-C", dest, *(["-m"] if touch else []), "-q")

        assert ((dest / "a.txt").stat().st_mtime == 1_000_000) != touch

    @pytest.mark.parametrize("depth,expect", [("2", EXIT_FAILED), ("3", EXIT_OK)])
    def test_max_depth_bounds_entry_paths(
        self, tmp_path: Path, depth: str, expect: int
    ) -> None:
        """Test that an entry nested deeper than --max-depth fails the archive."""
        archive = tmp_path / "deep.tar"
        make_tar(archive, file_entry("a/b/c.txt"))
        dest = tmp_path / "dest"

        invoke("extract", archive, "-C", dest, "--max-depth", depth, expect=expect)

        assert (dest / "a/b/c.txt").exists() == (expect == EXIT_OK)

    def test_missing_directory_is_created(
        self, payload: Path, tmp_path: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that a `-C` that doesn't exist is made for a single file, as it
        is for an archive."""
        packed = tmp_path / "a.gz"
        packed.write_bytes(gzip.compress(payload.read_bytes()))
        dest = tmp_path / "new" / "dir"

        invoke("extract", packed, make_archive("zip"), "-C", dest, "-q")

        assert (dest / "a").read_bytes() == payload.read_bytes()
        assert (dest / "tree").is_dir()

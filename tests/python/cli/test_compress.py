"""Tests for `compresso compress`."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from compresso.cli._render import (
    EXIT_FAILED,
    EXIT_USAGE,
)

from ..helpers import renamed
from .conftest import CONFLICT_MODES, CompressTarget, invoke


class TestCompressRoundTrip:
    """Test compress and decompress, end to end."""

    def test_compress_then_decompress(self, payload: Path, tmp_path: Path) -> None:
        """Test that a file survives a round trip through the CLI unchanged."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"

        invoke("compress", payload, "-o", archive, "-q")
        invoke("extract", archive, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    # The backends are covered in core and C tests; this checks the flags
    @pytest.mark.parametrize(
        "args",
        [("-a", "bzip2"), ("-a", "zstd", "-l", "19"), ("-s", "fast")],
        ids=["algo", "level", "strategy"],
    )
    def test_algorithms_round_trip(
        self, payload: Path, tmp_path: Path, args: tuple[str, ...]
    ) -> None:
        """Test that -a, -l and -s reach the backend."""
        archive = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"

        invoke("compress", payload, "-o", archive, *args, "-q")
        invoke("extract", archive, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()


class TestCompOnlyFlags:
    """Test that -a and -s, which pick the .comp codec, are refused elsewhere."""

    def test_algo_with_strategy(self, payload: Path) -> None:
        """Test that -a and -s together are a usage error."""
        result = invoke(
            "compress", payload, "-a", "zstd", "-s", "fast", expect=EXIT_USAGE
        )

        assert "-a contradicts -s" in result.output

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("-a", "zstd", "-F", "gz"), "-a applies only to .comp, not gz"),
            (("-s", "fast", "-o", "x.zip"), "-s applies only to .comp, not zip"),
        ],
        ids=["algo-standalone", "strategy-archive"],
    )
    def test_refused_outside_comp(
        self,
        payload: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        args: tuple[str, ...],
        message: str,
    ) -> None:
        """Test that a format with its own codec refuses -a and -s, writing nothing."""
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.iterdir())

        result = invoke("compress", payload, *args, expect=EXIT_USAGE)

        assert message in result.output
        assert set(tmp_path.iterdir()) == before


class TestCompressConflicts:
    """Test what `compress` does when its output already exists."""

    @CONFLICT_MODES
    def test_existing_output(
        self,
        compress_target: CompressTarget,
        flags: tuple[str, ...],
        expect: int,
        kept: bool,
        renames: bool,
    ) -> None:
        """Test that each overwrite mode keeps, replaces or renames around the
        output, and the summary line says which."""
        t = compress_target
        t.dest.write_bytes(b"stale")

        result = invoke(
            "compress", t.source, "-o", t.dest, *t.args, *flags, expect=expect
        )

        sibling = t.dest.parent / renamed(t.dest.name)
        assert (t.dest.read_bytes() == b"stale") == kept
        assert sibling.is_file() == renames
        assert (f"output renamed to {sibling}" in result.output) == renames
        assert (f"{t.dest}  skipped" in result.output) == ("skip" in flags)


class TestDereference:
    """Test `-h`."""

    @pytest.mark.usefixtures("needs_symlinks")
    @pytest.mark.parametrize("flag", ["-h", "--dereference"])
    def test_archive_holds_the_target(self, tmp_path: Path, flag: str) -> None:
        """Test that the flag reaches the archive."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a").write_text("hello")
        (tree / "link").symlink_to("a")
        output = tmp_path / "out.tar"

        invoke("compress", tree, "-o", output, flag, "-q")

        with tarfile.open(output) as tar:
            assert tar.getmember("tree/link").isfile()


class TestRemoveInput:
    """Test what `--rm` and `-k` do to the input."""

    @pytest.mark.parametrize(
        "flags,removes",
        [
            ((), False),
            (("--rm",), True),
            (("-k",), False),
            (("--rm", "--keep"), False),
            (("-k", "--rm"), True),
        ],
        ids=["default", "rm", "keep", "rm-then-keep", "keep-then-rm"],
    )
    def test_flags(
        self,
        compress_target: CompressTarget,
        flags: tuple[str, ...],
        removes: bool,
    ) -> None:
        """Test that the input goes only with --rm, the last flag winning."""
        t = compress_target

        invoke("compress", t.source, "-o", t.dest, *t.args, *flags, "-q")

        assert t.dest.is_file()
        assert t.source.exists() != removes

    def test_a_failed_run_keeps_the_input(
        self, compress_target: CompressTarget
    ) -> None:
        """Test that an output refused by --on-conflict leaves the input."""
        t = compress_target
        t.dest.write_bytes(b"stale")

        invoke(
            "compress",
            t.source,
            "-o",
            t.dest,
            *t.args,
            "--on-conflict",
            "error",
            "--rm",
            expect=EXIT_FAILED,
        )

        assert t.source.exists()

    def test_skip_is_refused(self, payload: Path) -> None:
        """Test that --rm can't lose an input to an output that was skipped."""
        result = invoke(
            "compress", payload, "--on-conflict", "skip", "--rm", expect=EXIT_USAGE
        )

        assert "--rm contradicts --on-conflict skip" in result.output
        assert payload.exists()

    def test_undeletable_file_follows_the_summary(
        self, locked: Path, tmp_path: Path
    ) -> None:
        """Test that an input that won't go is a warning after a normal summary."""
        source = locked / "a.txt"
        source.write_text("a")
        locked.chmod(0o555)
        out = tmp_path / "a.gz"

        result = invoke("compress", source, "-o", out, "--rm", expect=EXIT_FAILED)

        assert out.is_file()
        assert source.exists()
        warning = f"! {source}: not removed: Permission denied"
        assert result.output.index("→") < result.output.index(warning)

    @pytest.mark.parametrize("flags", [(), ("-q",)], ids=["default", "quiet"])
    def test_undeletable_archive_source_names_what_was_written(
        self,
        locked: Path,
        source_tree: Path,
        tmp_path: Path,
        flags: tuple[str, ...],
    ) -> None:
        """Test that the archive isn't called failed, and a rename is reported."""
        stuck = locked / "a.txt"
        stuck.write_text("a")
        locked.chmod(0o555)
        out = tmp_path / "out.tar"
        out.write_bytes(b"stale")

        result = invoke(
            "compress",
            source_tree,
            locked,
            "-o",
            out,
            "--rm",
            *flags,
            expect=EXIT_FAILED,
        )

        written = tmp_path / renamed("out.tar")
        assert tarfile.is_tarfile(written)
        assert out.read_bytes() == b"stale"
        assert not source_tree.exists()
        assert stuck.exists()
        assert f"! {stuck}: not removed: Permission denied" in result.output
        assert "Archive failed" not in result.output
        assert (written.name in result.output) != bool(flags)


class TestSmartCompress:
    """Test `compress` runs the job dispatch plans; the rules are in frontend/test_dispatch.py."""

    def test_a_directory_round_trips_as_an_archive(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a directory is written as the default archive and comes back."""
        invoke("compress", source_tree, "-q")

        dest = tmp_path / "back"
        invoke(
            "extract",
            source_tree.with_name(f"{source_tree.name}.zip"),
            "-C",
            dest,
            "-q",
        )

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_single_file_format_refuses_several_inputs(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a .gz holds one file, so a directory cannot go into one."""
        result = invoke(
            "compress",
            source_tree,
            "-F",
            "gz",
            "-o",
            tmp_path / "o.gz",
            expect=EXIT_USAGE,
        )

        assert "single file" in result.output

    def test_filters_narrow_an_archive(self, source_tree: Path, tmp_path: Path) -> None:
        """Test that repeated --include and --exclude reach the archive."""
        output = tmp_path / "o.tar"

        invoke(
            "compress",
            source_tree,
            "-o",
            output,
            "--include",
            "f0.bin",
            "--include",
            "f1.bin",
            "--exclude",
            "f1.bin",
            "-q",
        )

        with tarfile.open(output) as tar:
            assert sorted(tar.getnames()) == ["tree", "tree/f0.bin"]

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_filters_are_refused_for_a_single_file(
        self, payload: Path, flag: str
    ) -> None:
        """Test that a glob has nothing to select in a one-file container."""
        result = invoke("compress", payload, flag, "x", expect=EXIT_USAGE)

        assert "apply only to archives" in result.output

    def test_unknown_format_is_refused(self, payload: Path) -> None:
        """Test that an unrecognised -F names neither container."""
        invoke("compress", payload, "-F", "bogus", expect=EXIT_USAGE)

"""Tests for the Compresso CLI."""

from __future__ import annotations

import gzip
import os
import subprocess
import sys
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner, Result

import compresso.cli.algos as algos_cmd
import compresso.cli.compress as compress_cmd
import compresso.cli.extract as extract_cmd
import compresso.cli.inspect as inspect_cmd
from compresso.cli import app
from compresso.cli._render import (
    BAR_THRESHOLD,
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    format_size,
    format_time,
    progress_bar,
)
from compresso.cli.extract import list_entries
from compresso.frontend._job import ProgressCallback
from compresso.frontend.api import CompressionJob
from compresso.frontend.archive_api import ArchiveEntry, ArchiveJob, ArchiveOptions

from .helpers import renamed

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
def payload(temp_dir: Path) -> Path:
    """A small incompressible file, below the progress bar threshold.

    Args:
        temp_dir: The temporary directory to use for the payload.

    Returns:
        The path to the payload file.
    """
    path = temp_dir / "payload.bin"
    path.write_bytes(os.urandom(PAYLOAD_SIZE))

    return path


@pytest.fixture
def source_tree(temp_dir: Path) -> Path:
    """A small directory tree to archive.

    Function-scoped because some tests delete it.

    Args:
        temp_dir: The temporary directory to use for the source tree.

    Returns:
        The path to the source tree root.
    """
    root = temp_dir / "tree"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.bin").write_bytes(os.urandom(TREE_FILE_SIZE))

    return root


@pytest.fixture
def make_archive(source_tree: Path, temp_dir: Path) -> Callable[[str], Path]:
    """Build an archive of `source_tree` as input for a test.

    Built through the API rather than the CLI, and checked, so a broken setup
    cannot surface as a confusing failure later in the test.

    Args:
        source_tree: The tree to archive.
        temp_dir: The directory to write the archive in.

    Returns:
        A function taking the format and returning the archive's path.
    """

    def make(fmt: str) -> Path:
        archive = temp_dir / f"out.{fmt}"
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
    request: pytest.FixtureRequest, payload: Path, source_tree: Path, temp_dir: Path
) -> CompressTarget:
    """A single file into a .comp container, or a tree into a tar archive.

    The two take different paths through `compress`, but share its
    overwrite-mode handling.

    Args:
        request: Pytest request object.
        payload: The single-file input.
        source_tree: The directory input.
        temp_dir: The directory to write the output in.

    Returns:
        The input, output and any extra arguments for the call.
    """
    if request.param == "file":
        return CompressTarget(payload, temp_dir / "out.comp", ())

    return CompressTarget(source_tree, temp_dir / "out.tar", ("-f", "tar"))


@dataclass
class Bar:
    """One progress bar a command opened."""

    enabled: bool
    updates: int = 0


@pytest.fixture
def bars(monkeypatch: pytest.MonkeyPatch) -> list[Bar]:
    """Record each progress bar a command opens, still drawing the real one.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The bars opened so far, in order.
    """
    opened: list[Bar] = []

    @contextmanager
    def spy(
        label: str, total: int, *, enabled: bool
    ) -> Iterator[ProgressCallback | None]:
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

    # Each command imports progress_bar by name, so each binding is patched
    monkeypatch.setattr(compress_cmd, "progress_bar", spy)
    monkeypatch.setattr(extract_cmd, "progress_bar", spy)

    return opened


class TestFormatters:
    """Test utility formatting functions."""

    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, "0.00 B"),
            (500, "500.00 B"),
            (1024, "1.00 KB"),
            (1536, "1.50 KB"),
            (1048576, "1.00 MB"),
            (1073741824, "1.00 GB"),
            (1099511627776, "1.00 TB"),
        ],
    )
    def test_format_size(self, size: int, expected: str):
        """Test size formatting with various values."""
        result = format_size(size)
        assert result == expected

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0.0005, "0ms"),
            (0.5, "500ms"),
            (1.0, "1.00s"),
            (5.5, "5.50s"),
            (59.9, "59.90s"),
            (60.0, "1m 0.0s"),
            (90.5, "1m 30.5s"),
            (125.0, "2m 5.0s"),
        ],
    )
    def test_format_time(self, seconds: float, expected: str):
        """Test time formatting with various values."""
        assert format_time(seconds) == expected


class TestAppStructure:
    """Test the package assembles into one app with every command registered."""

    @pytest.mark.parametrize(
        "name",
        [
            "compress",
            "decompress",
            "archive",
            "extract",
            "inspect",
            "benchmark",
            "list",
        ],
    )
    def test_command_is_registered(self, name: str) -> None:
        """Test that each command appears in the top-level help."""
        assert name in invoke("--help").output

    def test_commands_are_listed_in_workflow_order(self) -> None:
        """Test that the order is chosen in `__init__`, not inherited from import sorting.

        Alphabetical imports would put `list` first.
        """
        output = invoke("--help").output

        # Matched on the alias column, which is unique per row; a bare command
        # name also appears inside other commands' descriptions
        positions = [
            output.index(aliases)
            for aliases in [
                "(c, comp",
                "(x, ex",
                "(i, info)",
                "(b, bench)",
                "(l, ls)",
            ]
        ]
        assert positions == sorted(positions)

    @pytest.mark.parametrize(
        "alias,command",
        [
            ("c", "compress"),
            ("comp", "compress"),
            ("d", "decompress"),
            ("a", "archive"),
            ("x", "extract"),
            ("i", "inspect"),
            ("b", "benchmark"),
            ("ls", "list"),
        ],
    )
    def test_aliases_still_resolve(self, alias: str, command: str) -> None:
        """Test that each short name reaches the command it stands for.

        Help is compared without its usage line, which echoes the name typed.
        """

        def help_body(name: str) -> list[str]:
            lines = invoke(name, "--help").output.splitlines()
            return [line for line in lines if "Usage:" not in line]

        assert help_body(alias) == help_body(command)


class TestProgressBar:
    """Test the bar is drawn only for a large job that is not asked to be quiet."""

    @pytest.mark.parametrize(
        "size,quiet,expected",
        [
            (LARGE_PAYLOAD_SIZE, False, True),
            (LARGE_PAYLOAD_SIZE, True, False),
            (PAYLOAD_SIZE, False, False),
        ],
        ids=["large", "large-quiet", "small"],
    )
    def test_compress_bar_follows_size_and_quiet(
        self, temp_dir: Path, bars: list[Bar], size: int, quiet: bool, expected: bool
    ) -> None:
        """Test that the bar needs both a job past the threshold and no --quiet."""
        source = temp_dir / "source.bin"
        source.write_bytes(os.urandom(size))

        invoke(
            "compress", source, "-o", temp_dir / "out.comp", *(["-q"] if quiet else [])
        )

        assert [bar.enabled for bar in bars] == [expected]

    def test_file_round_trip_draws_both_bars(
        self, temp_dir: Path, bars: list[Bar]
    ) -> None:
        """Test that compress and extract each drive a bar with real progress."""
        source = temp_dir / "source.bin"
        source.write_bytes(os.urandom(LARGE_PAYLOAD_SIZE))
        packed = temp_dir / "out.comp"
        restored = temp_dir / "restored.bin"

        invoke("compress", source, "-o", packed)
        invoke("extract", packed, "-o", restored)

        assert [bar.enabled for bar in bars] == [True, True]
        assert all(bar.updates > 0 for bar in bars)
        assert restored.read_bytes() == source.read_bytes()

    def test_archive_round_trip_draws_both_bars(
        self, temp_dir: Path, bars: list[Bar]
    ) -> None:
        """Test that archiving and extracting a tree each drive a bar too."""
        tree = temp_dir / "big_tree"
        tree.mkdir()
        for i in range(2):
            (tree / f"f{i}.bin").write_bytes(os.urandom(LARGE_PAYLOAD_SIZE // 2))
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"

        invoke("compress", tree, "-o", archive, "-f", "tar")
        invoke("extract", archive, "-o", dest)

        assert [bar.enabled for bar in bars] == [True, True]
        assert all(bar.updates > 0 for bar in bars)
        for original in tree.iterdir():
            assert (dest / tree.name / original.name).read_bytes() == (
                original.read_bytes()
            )


class TestCompressRoundTrip:
    """Test compress and decompress, end to end."""

    def test_compress_then_decompress(self, payload: Path, temp_dir: Path) -> None:
        """Test that a file survives a round trip through the CLI unchanged."""
        archive = temp_dir / "out.comp"
        restored = temp_dir / "restored.bin"

        result = invoke("compress", payload, "-o", archive)
        assert "Compression successful" in result.output

        result = invoke("decompress", archive, "-o", restored)
        assert "Decompression successful" in result.output

        assert restored.read_bytes() == payload.read_bytes()

    def test_quiet_suppresses_output(self, payload: Path, temp_dir: Path) -> None:
        """Test that --quiet prints nothing on success."""
        result = invoke("compress", payload, "-o", temp_dir / "q.comp", "-q")
        assert result.output.strip() == ""

    @pytest.mark.parametrize("algo", ["zlib", "zstd", "lz4", "bzip2", "lzma"])
    def test_algorithms_round_trip(
        self, payload: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test that each backend is reachable through the CLI."""
        archive = temp_dir / f"{algo}.comp"
        restored = temp_dir / f"{algo}.out"

        invoke("compress", payload, "-o", archive, "-a", algo, "-q")
        invoke("decompress", archive, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    def test_level_above_nine_round_trips(self, payload: Path, temp_dir: Path) -> None:
        """Test that a level above the old 0-9 cap reaches a backend that takes it."""
        archive = temp_dir / "zstd19.comp"
        restored = temp_dir / "zstd19.out"

        invoke("compress", payload, "-o", archive, "-a", "zstd", "-l", "19", "-q")
        invoke("decompress", archive, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()


class TestArchiveRoundTrip:
    """Test archive and extract, end to end."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "tar.zst", "zip"])
    def test_archive_then_extract(
        self, source_tree: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that a tree survives a round trip through each archive format."""
        archive = temp_dir / f"out.{fmt}"
        dest = temp_dir / f"dest.{fmt}"

        result = invoke("compress", source_tree, "-o", archive, "-f", fmt)
        assert "Archive created" in result.output

        result = invoke("extract", archive, "-o", dest)
        assert "Extraction successful" in result.output

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_extract_list_only(
        self,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that --list prints each entry's size and path without writing anything."""
        archive = make_archive("tar")
        dest = temp_dir / "dest"

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

    def test_extract_renames_clashing_top_level_dir_by_default(
        self,
        source_tree: Path,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that a repeat extraction renames the clashing top-level directory."""
        archive = make_archive("tar")
        dest = temp_dir / "dest"

        invoke("extract", archive, "-o", dest, "-q")
        before = sorted(p.name for p in dest.rglob("*") if p.is_file())

        invoke("extract", archive, "-o", dest, "-q")

        after = sorted(p.name for p in dest.rglob("*") if p.is_file())
        assert len(after) == 2 * len(before)
        # No individual file was renamed; the set of file names is unchanged
        assert after == sorted(before + before)

        top_level = sorted(p.name for p in dest.iterdir())
        assert top_level == sorted([source_tree.name, renamed(source_tree.name)])

        renamed_dir = dest / renamed(source_tree.name)
        assert sorted(p.name for p in renamed_dir.iterdir()) == sorted(
            p.name for p in (dest / source_tree.name).iterdir()
        )

    def test_extract_error_on_conflict_still_refuses(
        self, temp_dir: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --error-on-conflict reproduces the old refuse-by-default behavior."""
        archive = make_archive("tar")
        dest = temp_dir / "dest"

        invoke("extract", archive, "-o", dest, "-q")
        invoke(
            "extract",
            archive,
            "-o",
            dest,
            "--error-on-conflict",
            "-q",
            expect=EXIT_FAILED,
        )

    @pytest.mark.parametrize("flag", ["--overwrite", "--skip-existing"])
    def test_extract_over_existing_with_a_flag(
        self, temp_dir: Path, make_archive: Callable[[str], Path], flag: str
    ) -> None:
        """Test that either flag makes a repeat extraction succeed."""
        archive = make_archive("tar")
        dest = temp_dir / "dest"
        invoke("extract", archive, "-o", dest, "-q")

        invoke("extract", archive, "-o", dest, flag, "-q")

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    @pytest.mark.parametrize("exact", [False, True], ids=["umask", "same-perms"])
    def test_same_permissions_skips_the_umask(
        self, temp_dir: Path, exact: bool
    ) -> None:
        """Test that -p restores an entry's mode without the umask applied."""
        archive = temp_dir / "modes.tar"
        info = tarfile.TarInfo("a.txt")
        info.mode = 0o777
        with tarfile.open(archive, "w") as tar:
            tar.addfile(info)
        dest = temp_dir / "dest"

        invoke("extract", archive, "-o", dest, *(["-p"] if exact else []), "-q")

        umask = os.umask(0)
        os.umask(umask)
        expected = 0o777 if exact else 0o777 & ~umask
        assert (dest / "a.txt").stat().st_mode & 0o7777 == expected


class TestCompressConflicts:
    """Test what `compress` does when its output already exists."""

    def test_renames_clashing_output_by_default(
        self, compress_target: CompressTarget
    ) -> None:
        """Test that compressing twice renames the second output rather than
        overwriting the first."""
        t = compress_target
        invoke("compress", t.source, "-o", t.dest, *t.args, "-q")
        original_bytes = t.dest.read_bytes()

        invoke("compress", t.source, "-o", t.dest, *t.args, "-q")

        assert t.dest.read_bytes() == original_bytes
        assert (t.dest.parent / renamed(t.dest.name)).is_file()

    def test_error_on_conflict_refuses_existing_output(
        self, compress_target: CompressTarget
    ) -> None:
        """Test that --error-on-conflict refuses to touch an existing output."""
        t = compress_target
        invoke("compress", t.source, "-o", t.dest, *t.args, "-q")
        original_bytes = t.dest.read_bytes()

        invoke(
            "compress",
            t.source,
            "-o",
            t.dest,
            *t.args,
            "--error-on-conflict",
            "-q",
            expect=EXIT_FAILED,
        )

        assert t.dest.read_bytes() == original_bytes

    def test_skip_existing_leaves_the_output_untouched(
        self, compress_target: CompressTarget
    ) -> None:
        """Test that --skip-existing leaves the first output as-is and still succeeds."""
        t = compress_target
        invoke("compress", t.source, "-o", t.dest, *t.args, "-q")
        original_bytes = t.dest.read_bytes()

        invoke("compress", t.source, "-o", t.dest, *t.args, "--skip-existing", "-q")

        assert t.dest.read_bytes() == original_bytes


class TestDecompressConflicts:
    """Test what decompressing one file does when its output already exists."""

    def test_renames_clashing_output_by_default(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that an existing output is kept and the new one renamed."""
        archive = temp_dir / "out.comp"
        restored = temp_dir / "restored.bin"
        invoke("compress", payload, "-o", archive, "-q")
        restored.write_bytes(b"stale")

        result = invoke("decompress", archive, "-o", restored)

        sibling = temp_dir / renamed(restored.name)
        assert f"Renamed to:        {sibling}" in result.output
        assert restored.read_bytes() == b"stale"
        assert sibling.read_bytes() == payload.read_bytes()

    def test_overwrite_replaces_the_existing_output(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that --overwrite replaces the existing output."""
        archive = temp_dir / "out.comp"
        restored = temp_dir / "restored.bin"
        invoke("compress", payload, "-o", archive, "-q")
        restored.write_bytes(b"stale")

        invoke("decompress", archive, "-o", restored, "--overwrite", "-q")

        assert restored.read_bytes() == payload.read_bytes()


class TestInspectAndList:
    """Test the read-only commands."""

    def test_inspect_reports_a_valid_file(self, payload: Path, temp_dir: Path) -> None:
        """Test that inspect describes a file the CLI just wrote."""
        archive = temp_dir / "out.comp"
        invoke("compress", payload, "-o", archive, "-q")

        result = invoke("inspect", archive)

        assert "Valid Compresso file" in result.output

    def test_inspect_json(self, payload: Path, temp_dir: Path) -> None:
        """Test that --json emits parseable output."""
        archive = temp_dir / "out.comp"
        invoke("compress", payload, "-o", archive, "-q")

        result = invoke("inspect", archive, "--json")

        assert orjson.loads(result.output)["is_compresso"] is True

    def test_runs_as_a_module(self) -> None:
        """Test that `python -m compresso.cli` works, which a package needs __main__ for."""
        result = subprocess.run(
            [sys.executable, "-m", "compresso.cli", "list"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == EXIT_OK
        assert "zstd" in result.stdout

    def test_list_shows_level_ranges(self) -> None:
        """Test that list shows each backend's levels, and none for snappy."""
        output = invoke("list").output

        zstd = output[output.index("- zstd") :].split("\n\n")[0]
        snappy = output[output.index("- snappy") :].split("\n\n")[0]
        assert "Levels: 1-22" in zstd
        assert "Levels: none" in snappy

    def test_inspect_archive_summary_omits_entries(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that inspecting an archive shows only a summary by default."""
        result = invoke("inspect", make_archive("zip"))

        assert "Format:" in result.output
        assert "Entries:       4" in result.output
        assert "f0.bin" not in result.output

    def test_inspect_archive_json_omits_entries_by_default(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --json without --entries has no entries key."""
        result = invoke("inspect", make_archive("zip"), "--json")

        data = orjson.loads(result.output)
        assert data["is_archive"] is True
        assert data["entry_count"] == 4
        assert "entries" not in data

    def test_inspect_entries_lists_each_entry(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --entries lists each entry's own detail."""
        result = invoke("inspect", make_archive("zip"), "--entries")

        assert "f0.bin" in result.output

    def test_inspect_entries_json_lists_each_entry(
        self, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that --entries --json includes a populated entries list."""
        result = invoke("inspect", make_archive("zip"), "--entries", "--json")

        data = orjson.loads(result.output)
        assert len(data["entries"]) == 4
        assert any("f0.bin" in entry["path"] for entry in data["entries"])


class TestExitCodes:
    """Test 0 success, 1 the operation failed, 2 the request could not be made."""

    def test_missing_input_is_usage(self, temp_dir: Path) -> None:
        """Test that compressing a file that is not there is a usage error."""
        invoke("compress", temp_dir / "nope.bin", expect=EXIT_USAGE)

    def test_decompressing_a_non_compresso_file_is_usage(self, payload: Path) -> None:
        """Test that the input is wrong, rather than the operation having failed."""
        invoke("decompress", payload, expect=EXIT_USAGE)

    def test_inspecting_a_non_compresso_file_is_usage(self, payload: Path) -> None:
        """Test the same for inspect."""
        invoke("inspect", payload, expect=EXIT_USAGE)

    def test_extracting_a_non_archive_is_usage(self, payload: Path) -> None:
        """Test the same for extract."""
        invoke("extract", payload, expect=EXIT_USAGE)

    @pytest.mark.parametrize("command", ["compress", "extract"])
    @pytest.mark.parametrize(
        "flags",
        [
            ["--overwrite", "--skip-existing"],
            ["--overwrite", "--error-on-conflict"],
            ["--skip-existing", "--error-on-conflict"],
        ],
        ids=["overwrite+skip", "overwrite+error", "skip+error"],
    )
    def test_mutually_exclusive_flag_usage(
        self,
        source_tree: Path,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
        command: str,
        flags: list[str],
    ) -> None:
        """Test that combining any two overwrite-mode flags is contradictory."""
        if command == "compress":
            args = [source_tree, "-o", temp_dir / "out.tar", "-f", "tar"]
        else:
            args = [make_archive("tar"), "-o", temp_dir / "d"]

        result = invoke(command, *args, *flags, expect=EXIT_USAGE)
        assert "mutually exclusive" in result.output

    @pytest.mark.parametrize(
        "args,message",
        [
            (["-a", "zlib", "-l", "15"], "zlib compression level 15 out of range (0-9"),
            (["-a", "snappy", "-l", "3"], "snappy has no compression levels"),
            (["-f", "gz", "-l", "10"], "gzip compression level 10 out of range"),
        ],
    )
    def test_out_of_range_level_is_usage(
        self, payload: Path, temp_dir: Path, args: list[str], message: str
    ) -> None:
        """Test that a level the backend rejects is refused up front, naming the range."""
        dest = temp_dir / "out.bin"
        result = invoke("compress", payload, "-o", dest, *args, expect=EXIT_USAGE)
        assert message in result.output
        assert not dest.exists()

    def test_out_of_range_archive_level_is_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that an archive's level is checked against the stage that compresses."""
        dest = temp_dir / "out.tar"
        result = invoke(
            "compress",
            source_tree,
            "-o",
            dest,
            "-f",
            "tar",
            "-l",
            "5",
            expect=EXIT_USAGE,
        )
        assert "tar has no compression levels" in result.output
        assert not dest.exists()

    def test_bad_benchmark_level_is_usage(self, payload: Path) -> None:
        """Test that a level that is not a number is a usage error."""
        invoke("benchmark", payload, "--levels", "nope", expect=EXIT_USAGE)

    def test_unknown_archive_format_is_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a format that cannot hold multiple entries is refused up front."""
        invoke(
            "compress",
            source_tree,
            "-o",
            temp_dir / "o.gz",
            "-f",
            "gz",
            expect=EXIT_USAGE,
        )

    def test_recognised_but_unsupported_archive_names_itself(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a detected-but-unwritable container reports its own name."""
        dest = temp_dir / "o.7z"
        result = invoke(
            "compress", source_tree, "-o", dest, "-f", "7z", expect=EXIT_FAILED
        )
        assert "7z archives are recognised but not supported yet" in result.output
        assert not dest.exists()

    def test_unsupported_archive_suffix_is_not_written_as_zip(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that `-o x.7z` alone names 7z rather than writing a zip there."""
        dest = temp_dir / "o.7z"
        result = invoke("compress", source_tree, "-o", dest, expect=EXIT_FAILED)
        assert "7z archives are recognised but not supported yet" in result.output
        assert not dest.exists()

    def test_failed_operation_is_one(
        self, temp_dir: Path, make_archive: Callable[[str], Path]
    ) -> None:
        """Test that a job that starts and then fails exits 1, not 2."""
        archive = make_archive("tar")
        dest = temp_dir / "dest"
        invoke("extract", archive, "-o", dest, "-q")

        invoke(
            "extract",
            archive,
            "-o",
            dest,
            "--error-on-conflict",
            "-q",
            expect=EXIT_FAILED,
        )


class TestInterruption:
    """Test Ctrl-C reports and exits 130.

    ExtendedTyper catches KeyboardInterrupt around the command callback and turns
    it into a silent `Exit(130)`, so each command catches it first to say what
    was interrupted.
    """

    @pytest.mark.parametrize(
        "command,expected",
        [
            (["compress"], "Compression cancelled"),
            (["inspect"], "Inspection cancelled"),
            (["list"], "Listing cancelled"),
        ],
    )
    def test_interrupt_is_reported(
        self,
        payload: Path,
        monkeypatch: pytest.MonkeyPatch,
        command: list[str],
        expected: str,
    ) -> None:
        """Test that each command names itself rather than dying silently."""

        # Interrupt the first real call each command makes
        def interrupt(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(CompressionJob, "from_file", interrupt)
        monkeypatch.setattr(inspect_cmd, "inspect_file", interrupt)
        monkeypatch.setattr(algos_cmd, "list_capabilities", interrupt)

        args: list[str | Path] = [*command]
        if command[0] != "list":
            args.append(payload)

        result = invoke(*args, expect=EXIT_CANCELLED)

        assert expected in result.output


class TestSmartCompress:
    """Test `compress` picks the job from its inputs and the format asked for."""

    def test_one_file_becomes_a_compresso_container(self, payload: Path) -> None:
        """Test that with no format, a single file gets the .comp container."""
        invoke("compress", payload, "-q")

        assert payload.with_suffix(payload.suffix + ".comp").exists()

    @pytest.mark.parametrize(
        "fmt,suffix",
        [
            ("gz", ".gz"),
            ("bz2", ".bz2"),
            ("xz", ".xz"),
            ("zst", ".zst"),
            ("lz4", ".lz4"),
        ],
    )
    def test_one_file_into_a_standalone_container(
        self, payload: Path, fmt: str, suffix: str
    ) -> None:
        """Test that -f names a single-file container, and the suffix follows from it."""
        invoke("compress", payload, "-f", fmt, "-q")

        assert payload.with_suffix(payload.suffix + suffix).exists()

    def test_standalone_output_is_a_real_gzip(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that the .gz written is the format, not merely the name."""
        output = temp_dir / "out.gz"
        invoke("compress", payload, "-o", output, "-q")

        with gzip.open(output, "rb") as f:
            assert f.read() == payload.read_bytes()

    def test_a_directory_becomes_an_archive(self, source_tree: Path) -> None:
        """Test that a directory needs a container that holds many entries."""
        invoke("compress", source_tree, "-q")

        assert (source_tree.parent / f"{source_tree.name}.zip").exists()

    def test_several_files_become_an_archive(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that multiple files become an archive."""
        second = temp_dir / "second.bin"
        second.write_bytes(os.urandom(1024))
        output = temp_dir / "both.tar"

        invoke("compress", payload, second, "-o", output, "-q")

        with tarfile.open(output) as tar:
            assert sorted(tar.getnames()) == [payload.name, second.name]

    @pytest.mark.parametrize(
        "name,expected_magic",
        [
            ("out.gz", b"\x1f\x8b"),
            ("out.zst", b"\x28\xb5\x2f\xfd"),
            ("out.zip", b"PK"),
        ],
    )
    def test_format_inferred_from_the_output_name(
        self, payload: Path, temp_dir: Path, name: str, expected_magic: bytes
    ) -> None:
        """Test that a recognisable extension on -o chooses the format by itself."""
        output = temp_dir / name

        invoke("compress", payload, "-o", output, "-q")

        assert output.read_bytes().startswith(expected_magic)

    def test_explicit_format_beats_the_output_name(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that -f overrides whatever the extension suggests."""
        output = temp_dir / "misleading.gz"

        invoke("compress", payload, "-o", output, "-f", "zst", "-q")

        assert output.read_bytes().startswith(b"\x28\xb5\x2f\xfd")

    def test_tar_zst_beats_zst_in_the_name(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that the longest matching suffix wins, so out.tar.zst is an archive."""
        output = temp_dir / "out.tar.zst"

        invoke("compress", source_tree, "-o", output, "-q")

        # Round-trips as an archive rather than as a lone compressed file
        dest = temp_dir / "back"
        invoke("extract", output, "-o", dest, "-q")
        assert (dest / source_tree.name).is_dir()

    def test_single_file_format_refuses_several_inputs(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a .gz holds one file, so a directory cannot go into one."""
        result = invoke(
            "compress",
            source_tree,
            "-f",
            "gz",
            "-o",
            temp_dir / "o.gz",
            expect=EXIT_USAGE,
        )

        assert "single file" in result.output

    def test_unknown_format_is_refused(self, payload: Path) -> None:
        """Test that an unrecognised -f names neither container."""
        invoke("compress", payload, "-f", "bogus", expect=EXIT_USAGE)


class TestSmartExtract:
    """Test `extract` picks the job from each file's own bytes."""

    @pytest.mark.parametrize("fmt", ["gz", "bz2", "xz", "zst", "lz4"])
    def test_standalone_round_trip(
        self, payload: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test every standalone container goes out and comes back."""
        packed = temp_dir / f"p.{fmt}"
        restored = temp_dir / f"back.{fmt}"

        invoke("compress", payload, "-o", packed, "-q")
        invoke("extract", packed, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    def test_dispatch_follows_the_bytes_not_the_name(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that a renamed .gz is still recognised as one."""
        packed = temp_dir / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        disguised = temp_dir / "mystery.dat"
        disguised.write_bytes(packed.read_bytes())
        restored = temp_dir / "back.bin"

        invoke("extract", disguised, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    def test_an_archive_named_like_anything_still_extracts(
        self,
        source_tree: Path,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that a renamed archive still extracts."""
        disguised = temp_dir / "riddle.bin"
        disguised.write_bytes(make_archive("tar").read_bytes())
        dest = temp_dir / "out"

        invoke("extract", disguised, "-o", dest, "-q")

        assert (dest / source_tree.name).is_dir()

    def test_an_archive_unpacks_beside_itself(
        self,
        source_tree: Path,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that with no -o, entries land next to the archive, not in the cwd."""
        packed = make_archive("tar")

        # The sources are beside the archive, so they would be in the way
        for f in source_tree.iterdir():
            f.unlink()
        source_tree.rmdir()

        invoke("extract", packed, "-q")

        assert (temp_dir / source_tree.name).is_dir()
        assert not (Path.cwd() / source_tree.name).exists()

    def test_output_directory_receives_a_single_file(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that -o naming an existing directory puts the file inside it."""
        packed = temp_dir / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        into = temp_dir / "into"
        into.mkdir()

        invoke("extract", packed, "-o", into, "-q")

        assert (into / "p").read_bytes() == payload.read_bytes()

    def test_several_inputs_in_one_go(self, payload: Path, temp_dir: Path) -> None:
        """Test that each input is dispatched on its own."""
        first = temp_dir / "a.gz"
        second = temp_dir / "b.zst"
        invoke("compress", payload, "-o", first, "-q")
        invoke("compress", payload, "-o", second, "-q")

        invoke("extract", first, second, "-q")

        assert (temp_dir / "a").read_bytes() == payload.read_bytes()
        assert (temp_dir / "b").read_bytes() == payload.read_bytes()

    def test_listing_a_single_file_container_is_refused(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that there is nothing to list in a container holding one file."""
        packed = temp_dir / "p.gz"
        invoke("compress", payload, "-o", packed, "-q")

        result = invoke("extract", packed, "--list", expect=EXIT_USAGE)

        assert "nothing to list" in result.output


class TestMergedVerbCompatibility:
    """Test that the old names still work, and the old argument order says so."""

    @pytest.mark.parametrize("name", ["archive", "a", "ar"])
    def test_archive_names_reach_compress(
        self, source_tree: Path, temp_dir: Path, name: str
    ) -> None:
        """Test that `archive` is an alias of `compress`."""
        output = temp_dir / "out.tar.zst"

        invoke(name, source_tree, "-o", output, "-q")

        assert output.exists()

    @pytest.mark.parametrize("name", ["decompress", "d", "decomp"])
    def test_decompress_names_reach_extract(
        self, payload: Path, temp_dir: Path, name: str
    ) -> None:
        """Test that `decompress` is an alias of `extract`."""
        packed = temp_dir / "p.comp"
        restored = temp_dir / "back.bin"
        invoke("compress", payload, "-o", packed, "-q")

        invoke(name, packed, "-o", restored, "-q")

        assert restored.read_bytes() == payload.read_bytes()

    def test_decompress_now_handles_archives_too(
        self,
        source_tree: Path,
        temp_dir: Path,
        make_archive: Callable[[str], Path],
    ) -> None:
        """Test that the merged verb does not care which name was typed."""
        dest = temp_dir / "out"

        invoke("decompress", make_archive("tar"), "-o", dest, "-q")

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

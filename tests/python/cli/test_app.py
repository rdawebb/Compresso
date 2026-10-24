"""Tests for the CLI app as a whole, and what every command shares."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import compresso.cli.formats as formats_cmd
import compresso.cli.info as info_cmd
import compresso.cli.list as list_cmd
from compresso.cli._render import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_USAGE,
    format_size,
    format_time,
)
from compresso.frontend.api import CompressionJob, DecompressionJob
from compresso.frontend.archive_api import (
    ArchiveJob,
    ExtractJob,
)

from .conftest import LARGE_PAYLOAD_SIZE, PAYLOAD_SIZE, Bar, CompressTarget, invoke


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

    def test_commands_are_listed_in_workflow_order(self) -> None:
        """Test that the order is chosen in `__init__`, not inherited from import sorting.

        Alphabetical imports would put `benchmark` first.
        """
        output = invoke("--help").output

        # Matched on the alias column, which is unique per row, or the
        # description where there is none; a bare command name also appears
        # inside other commands' descriptions
        positions = [
            output.index(aliases)
            for aliases in [
                "(c)",
                "(x)",
                "(l)",
                "(i)",
                "List the available",
                "(b)",
            ]
        ]
        assert positions == sorted(positions)

    @pytest.mark.parametrize(
        "alias,command",
        [
            ("c", "compress"),
            ("x", "extract"),
            ("l", "list"),
            ("i", "info"),
            ("b", "benchmark"),
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
        self, tmp_path: Path, bars: list[Bar], size: int, quiet: bool, expected: bool
    ) -> None:
        """Test that the bar needs both a job past the threshold and no --quiet."""
        source = tmp_path / "source.bin"
        source.write_bytes(os.urandom(size))

        invoke(
            "compress", source, "-o", tmp_path / "out.comp", *(["-q"] if quiet else [])
        )

        assert [bar.enabled for bar in bars] == [expected]

    def test_file_round_trip_draws_both_bars(
        self, tmp_path: Path, bars: list[Bar]
    ) -> None:
        """Test that compress and extract each drive a bar with real progress."""
        source = tmp_path / "source.bin"
        source.write_bytes(os.urandom(LARGE_PAYLOAD_SIZE))
        packed = tmp_path / "out.comp"
        restored = tmp_path / "restored.bin"

        invoke("compress", source, "-o", packed)
        invoke("extract", packed, "-o", restored)

        assert [bar.enabled for bar in bars] == [True, True]
        assert all(bar.updates > 0 for bar in bars)
        assert restored.read_bytes() == source.read_bytes()

    def test_archive_round_trip_draws_both_bars(
        self, tmp_path: Path, bars: list[Bar]
    ) -> None:
        """Test that archiving and extracting a tree each drive a bar too."""
        tree = tmp_path / "big_tree"
        tree.mkdir()
        for i in range(2):
            (tree / f"f{i}.bin").write_bytes(os.urandom(LARGE_PAYLOAD_SIZE // 2))
        archive = tmp_path / "out.tar"
        dest = tmp_path / "dest"

        invoke("compress", tree, "-o", archive, "-F", "tar")
        invoke("extract", archive, "-o", dest)

        assert [bar.enabled for bar in bars] == [True, True]
        assert all(bar.updates > 0 for bar in bars)
        for original in tree.iterdir():
            assert (dest / tree.name / original.name).read_bytes() == (
                original.read_bytes()
            )


class TestExitCodes:
    """Test 0 success, 1 the operation failed, 2 the request could not be made."""

    @pytest.mark.parametrize(
        "command,name",
        [
            ("compress", "nope.bin"),
            ("info", "nope.comp"),
            ("extract", "nope.gz"),
            ("list", "nope.zip"),
        ],
    )
    def test_bad_input_is_usage(self, payload: Path, command: str, name: str) -> None:
        """Test that a missing input is wrong, rather than the operation having
        failed."""
        invoke(command, payload.parent / name, expect=EXIT_USAGE)

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX, not root"
    )
    @pytest.mark.parametrize(
        "command,shown", [("list", "f0.bin"), ("info", "Entries:")]
    )
    def test_unreadable_input_fails_alone(
        self,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        command: str,
        shown: str,
    ) -> None:
        """Test that an OSError from one input is reported against it, as gzip
        does, and the next input still runs."""
        locked = tmp_path / "locked.zip"
        locked.write_bytes(b"")
        locked.chmod(0)

        result = invoke(command, locked, make_archive("zip"), expect=EXIT_FAILED)

        assert f"{locked}: Permission denied" in result.output
        assert shown in result.stdout

    @pytest.mark.parametrize("command", ["compress", "extract"])
    @pytest.mark.parametrize("mode", ["rename", "skip", "error"])
    def test_force_with_another_conflict_mode_is_usage(
        self,
        source_tree: Path,
        tmp_path: Path,
        make_archive: Callable[[str], Path],
        command: str,
        mode: str,
    ) -> None:
        """Test that -f, meaning overwrite, contradicts any other --on-conflict."""
        if command == "compress":
            args = [source_tree, "-o", tmp_path / "out.tar", "-F", "tar"]
        else:
            args = [make_archive("tar"), "-o", tmp_path / "d"]

        result = invoke(command, *args, "-f", "--on-conflict", mode, expect=EXIT_USAGE)
        assert f"-f contradicts --on-conflict {mode}" in result.output

    # An archive's level is checked against the stage that compresses
    @pytest.mark.parametrize(
        "compress_target,args,message",
        [
            (
                "file",
                ["-a", "zlib", "-l", "15"],
                "zlib compression level 15 out of range (0-9",
            ),
            ("tree", ["-l", "5"], "tar has no compression levels"),
        ],
        indirect=["compress_target"],
    )
    def test_out_of_range_level_is_usage(
        self, compress_target: CompressTarget, args: list[str], message: str
    ) -> None:
        """Test that a level the backend rejects is refused up front, naming the range."""
        t = compress_target
        result = invoke(
            "compress", t.source, "-o", t.dest, *t.args, *args, expect=EXIT_USAGE
        )
        assert message in result.output
        assert not t.dest.exists()

    def test_bad_benchmark_level_is_usage(self, payload: Path) -> None:
        """Test that a level that is not a number is a usage error."""
        invoke("benchmark", payload, "--levels", "nope", expect=EXIT_USAGE)

    @pytest.mark.parametrize("flags", [(), ("-F", "7z")], ids=["suffix", "flag"])
    def test_recognised_but_unsupported_archive_names_itself(
        self, source_tree: Path, tmp_path: Path, flags: tuple[str, ...]
    ) -> None:
        """Test that 7z, asked for by -F or by `-o x.7z` alone, reports its own name
        rather than being written as a zip."""
        dest = tmp_path / "o.7z"
        result = invoke("compress", source_tree, "-o", dest, *flags, expect=EXIT_FAILED)
        assert "7z archives are recognised but not supported yet" in result.output
        assert not dest.exists()


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
            (["info"], "Inspection cancelled"),
            (["list"], "Listing cancelled"),
            (["formats"], "Format listing cancelled"),
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
        monkeypatch.setattr(info_cmd, "detect_format", interrupt)
        monkeypatch.setattr(list_cmd, "detect_format", interrupt)
        monkeypatch.setattr(formats_cmd, "list_capabilities", interrupt)

        args: list[str | Path] = [*command]
        if command[0] != "formats":
            args.append(payload)

        result = invoke(*args, expect=EXIT_CANCELLED)

        assert expected in result.output


class TestThreads:
    """Test that -T reaches each kind of job, and refuses a negative count."""

    def test_threads_reach_every_job(
        self,
        payload: Path,
        source_tree: Path,
        make_archive: Callable[[str], Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that compress and extract pass -T on, one file or many."""
        archive = make_archive("tar")
        seen: list[int] = []

        # Record the count each job was planned with, then stop before it runs
        def record(**kwargs: Any) -> None:
            options = kwargs.get("options")
            seen.append(kwargs["threads"] if options is None else options.threads)
            raise KeyboardInterrupt

        monkeypatch.setattr(CompressionJob, "from_file", record)
        monkeypatch.setattr(ArchiveJob, "from_paths", record)
        monkeypatch.setattr(DecompressionJob, "from_file", record)
        monkeypatch.setattr(ExtractJob, "from_archive", record)

        for command, source in [
            ("compress", payload),
            ("compress", source_tree),
            ("extract", payload),
            ("extract", archive),
        ]:
            invoke(command, source, "-T", "3", expect=EXIT_CANCELLED)

        assert seen == [3, 3, 3, 3]

    @pytest.mark.parametrize("command", ["compress", "extract"])
    def test_negative_threads_is_usage(self, payload: Path, command: str) -> None:
        """Test that a count below 0 is refused before any work starts."""
        invoke(command, payload, "-T", "-1", expect=EXIT_USAGE)

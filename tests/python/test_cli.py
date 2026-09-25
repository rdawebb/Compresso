"""Tests for the Compresso CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from compresso.cli import app
from compresso.cli._render import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    format_size,
    format_time,
)
from compresso.cli.extract import list_entries
from compresso.frontend.archive_api import ArchiveEntry

runner = CliRunner()

# Above the 1 MiB bar threshold, and incompressible so the archive stays large
PAYLOAD_SIZE = 2 * 1024 * 1024


@pytest.fixture
def payload(temp_dir: Path) -> Path:
    """A file big enough to exercise the progress path.

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

    Args:
        temp_dir: The temporary directory to use for the source tree.

    Returns:
        The path to the source tree root.
    """
    root = temp_dir / "tree"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.bin").write_bytes(os.urandom(PAYLOAD_SIZE // 2))

    return root


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
        "seconds,expected_pattern",
        [
            (0.0005, "ms"),  # Less than 1ms
            (0.5, "ms"),  # 500ms
            (1.0, "1.00s"),
            (5.5, "5.50s"),
            (59.9, "59.90s"),
            (60.0, "1m"),  # 1 minute
            (90.5, "1m 30"),  # 1m 30s
            (125.0, "2m"),  # 2m 5s
        ],
    )
    def test_format_time(self, seconds: float, expected_pattern: str):
        """Test time formatting with various values."""
        result = format_time(seconds)
        assert expected_pattern in result


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
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == EXIT_OK
        assert name in result.output

    def test_commands_are_listed_in_workflow_order(self) -> None:
        """Test that the order is chosen in `__init__`, not inherited from import sorting.

        Alphabetical imports would put `list` first.
        """
        output = runner.invoke(app, ["--help"]).output

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
        """Test that the short names keep working after the package split."""
        result = runner.invoke(app, [alias, "--help"])
        assert result.exit_code == EXIT_OK


class TestCompressRoundTrip:
    """Test compress and decompress, end to end."""

    def test_compress_then_decompress(self, payload: Path, temp_dir: Path) -> None:
        """Test that a file survives a round trip through the CLI unchanged."""
        archive = temp_dir / "out.comp"
        restored = temp_dir / "restored.bin"

        result = runner.invoke(app, ["compress", str(payload), "-o", str(archive)])
        assert result.exit_code == EXIT_OK
        assert "Compression successful" in result.output

        result = runner.invoke(app, ["decompress", str(archive), "-o", str(restored)])
        assert result.exit_code == EXIT_OK
        assert "Decompression successful" in result.output

        assert restored.read_bytes() == payload.read_bytes()

    def test_quiet_suppresses_output(self, payload: Path, temp_dir: Path) -> None:
        """Test that --quiet prints nothing on success."""
        result = runner.invoke(
            app, ["compress", str(payload), "-o", str(temp_dir / "q.comp"), "-q"]
        )
        assert result.exit_code == EXIT_OK
        assert result.output.strip() == ""

    @pytest.mark.parametrize("algo", ["zlib", "zstd", "lz4", "bzip2", "lzma"])
    def test_algorithms_round_trip(
        self, payload: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test that each backend is reachable through the CLI."""
        archive = temp_dir / f"{algo}.comp"
        restored = temp_dir / f"{algo}.out"

        assert (
            runner.invoke(
                app, ["compress", str(payload), "-o", str(archive), "-a", algo, "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert (
            runner.invoke(
                app, ["decompress", str(archive), "-o", str(restored), "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert restored.read_bytes() == payload.read_bytes()

    def test_level_above_nine_round_trips(self, payload: Path, temp_dir: Path) -> None:
        """Test that a level above the old 0-9 cap reaches a backend that takes it."""
        archive = temp_dir / "zstd19.comp"
        restored = temp_dir / "zstd19.out"

        result = runner.invoke(
            app,
            [
                "compress",
                str(payload),
                "-o",
                str(archive),
                "-a",
                "zstd",
                "-l",
                "19",
                "-q",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert (
            runner.invoke(
                app, ["decompress", str(archive), "-o", str(restored), "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert restored.read_bytes() == payload.read_bytes()

    def test_compress_renames_clashing_output_by_default(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that compressing twice to the same output renames the second file
        rather than overwriting the first."""
        dest = temp_dir / "out.comp"

        first = runner.invoke(app, ["compress", str(payload), "-o", str(dest), "-q"])
        assert first.exit_code == EXIT_OK
        original_bytes = dest.read_bytes()

        second = runner.invoke(app, ["compress", str(payload), "-o", str(dest), "-q"])
        assert second.exit_code == EXIT_OK

        suffix = " 2" if sys.platform == "darwin" else " (2)"
        renamed = temp_dir / f"out{suffix}.comp"

        assert dest.read_bytes() == original_bytes
        assert renamed.is_file()

    def test_compress_error_on_conflict_refuses_existing_output(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that --error-on-conflict refuses to touch an existing output."""
        dest = temp_dir / "out.comp"
        runner.invoke(app, ["compress", str(payload), "-o", str(dest), "-q"])

        result = runner.invoke(
            app,
            ["compress", str(payload), "-o", str(dest), "--error-on-conflict", "-q"],
        )
        assert result.exit_code == EXIT_FAILED

    def test_compress_skip_existing_leaves_the_output_untouched(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that --skip-existing leaves the first output as-is and still succeeds."""
        dest = temp_dir / "out.comp"
        runner.invoke(app, ["compress", str(payload), "-o", str(dest), "-q"])
        original_bytes = dest.read_bytes()

        result = runner.invoke(
            app,
            ["compress", str(payload), "-o", str(dest), "--skip-existing", "-q"],
        )

        assert result.exit_code == EXIT_OK
        assert dest.read_bytes() == original_bytes


class TestArchiveRoundTrip:
    """Test archive and extract, end to end."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "tar.zst", "zip"])
    def test_archive_then_extract(
        self, source_tree: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test that a tree survives a round trip through each archive format."""
        archive = temp_dir / f"out.{fmt}"
        dest = temp_dir / f"dest.{fmt}"

        result = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", fmt]
        )
        assert result.exit_code == EXIT_OK, result.output
        assert "Archive created" in result.output

        result = runner.invoke(app, ["extract", str(archive), "-o", str(dest)])
        assert result.exit_code == EXIT_OK, result.output
        assert "Extraction successful" in result.output

        for original in source_tree.iterdir():
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_extract_list_only(self, source_tree: Path, temp_dir: Path) -> None:
        """Test that --list prints each entry's size and path without writing anything."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )

        result = runner.invoke(
            app, ["extract", str(archive), "--list", "-o", str(dest)]
        )

        assert result.exit_code == EXIT_OK
        lines = result.output.splitlines()
        f0_line = next(line for line in lines if "f0.bin" in line)
        assert f0_line.split()[:2] == format_size(PAYLOAD_SIZE // 2).split()
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
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a repeat extraction renames the clashing top-level directory."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )

        first = runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])
        assert first.exit_code == EXIT_OK
        before = sorted(p.name for p in dest.rglob("*") if p.is_file())

        second = runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])
        assert second.exit_code == EXIT_OK

        after = sorted(p.name for p in dest.rglob("*") if p.is_file())
        assert len(after) == 2 * len(before)
        # No individual file was renamed; the set of file names is unchanged
        assert after == sorted(before + before)

        suffix = " 2" if sys.platform == "darwin" else " (2)"
        top_level = sorted(p.name for p in dest.iterdir())
        assert top_level == sorted([source_tree.name, source_tree.name + suffix])

        renamed_dir = dest / (source_tree.name + suffix)
        assert sorted(p.name for p in renamed_dir.iterdir()) == sorted(
            p.name for p in (dest / source_tree.name).iterdir()
        )

    def test_extract_error_on_conflict_still_refuses(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --error-on-conflict reproduces the old refuse-by-default behavior."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )

        assert (
            runner.invoke(
                app, ["extract", str(archive), "-o", str(dest), "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert (
            runner.invoke(
                app,
                [
                    "extract",
                    str(archive),
                    "-o",
                    str(dest),
                    "--error-on-conflict",
                    "-q",
                ],
            ).exit_code
            == EXIT_FAILED
        )

    def test_compress_renames_clashing_archive_by_default(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that archiving twice to the same output renames the second archive rather
        than overwriting the first."""
        archive = temp_dir / "out.tar"

        first = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )
        assert first.exit_code == EXIT_OK
        original_bytes = archive.read_bytes()

        second = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )
        assert second.exit_code == EXIT_OK

        suffix = " 2" if sys.platform == "darwin" else " (2)"
        renamed = temp_dir / f"out{suffix}.tar"

        assert archive.read_bytes() == original_bytes
        assert renamed.is_file()

    def test_compress_error_on_conflict_still_refuses(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --error-on-conflict refuses to touch an existing archive."""
        archive = temp_dir / "out.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )

        result = runner.invoke(
            app,
            [
                "compress",
                str(source_tree),
                "-o",
                str(archive),
                "-f",
                "tar",
                "--error-on-conflict",
                "-q",
            ],
        )
        assert result.exit_code == EXIT_FAILED

    def test_compress_skip_existing_leaves_the_archive_untouched(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --skip-existing leaves the first archive as-is and still succeeds."""
        archive = temp_dir / "out.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )
        original_bytes = archive.read_bytes()

        result = runner.invoke(
            app,
            [
                "compress",
                str(source_tree),
                "-o",
                str(archive),
                "-f",
                "tar",
                "--skip-existing",
                "-q",
            ],
        )

        assert result.exit_code == EXIT_OK
        assert archive.read_bytes() == original_bytes

    @pytest.mark.parametrize("flag", ["--overwrite", "--skip-existing"])
    def test_extract_over_existing_with_a_flag(
        self, source_tree: Path, temp_dir: Path, flag: str
    ) -> None:
        """Test that either flag makes a repeat extraction succeed."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )
        runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])

        result = runner.invoke(
            app, ["extract", str(archive), "-o", str(dest), flag, "-q"]
        )
        assert result.exit_code == EXIT_OK


class TestInspectAndList:
    """Test the read-only commands."""

    def test_inspect_reports_a_valid_file(self, payload: Path, temp_dir: Path) -> None:
        """Test that inspect describes a file the CLI just wrote."""
        archive = temp_dir / "out.comp"
        runner.invoke(app, ["compress", str(payload), "-o", str(archive), "-q"])

        result = runner.invoke(app, ["inspect", str(archive)])

        assert result.exit_code == EXIT_OK
        assert "Valid Compresso file" in result.output

    def test_inspect_json(self, payload: Path, temp_dir: Path) -> None:
        """Test that --json emits parseable output."""
        import json

        archive = temp_dir / "out.comp"
        runner.invoke(app, ["compress", str(payload), "-o", str(archive), "-q"])

        result = runner.invoke(app, ["inspect", str(archive), "--json"])

        assert result.exit_code == EXIT_OK
        assert json.loads(result.output)["is_compresso"] is True

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

    def test_list_shows_backends(self) -> None:
        """Test that list names the compiled-in algorithms."""
        result = runner.invoke(app, ["list"])

        assert result.exit_code == EXIT_OK
        assert "zstd" in result.output

    def test_list_shows_level_ranges(self) -> None:
        """Test that list shows each backend's levels, and none for snappy."""
        output = runner.invoke(app, ["list"]).output

        zstd = output[output.index("● zstd") :].split("\n\n")[0]
        snappy = output[output.index("● snappy") :].split("\n\n")[0]
        assert "Levels: 1-22" in zstd
        assert "Levels: none" in snappy

    def test_inspect_archive_summary_omits_entries(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that inspecting an archive shows only a summary by default."""
        archive = temp_dir / "out.zip"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "zip", "-q"]
        )

        result = runner.invoke(app, ["inspect", str(archive)])

        assert result.exit_code == EXIT_OK
        assert "Format:" in result.output
        assert "Entries:       4" in result.output
        assert "f0.bin" not in result.output

    def test_inspect_archive_json_omits_entries_by_default(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --json without --entries has no entries key."""
        import json

        archive = temp_dir / "out.zip"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "zip", "-q"]
        )

        result = runner.invoke(app, ["inspect", str(archive), "--json"])

        assert result.exit_code == EXIT_OK
        data = json.loads(result.output)
        assert data["is_archive"] is True
        assert data["entry_count"] == 4
        assert "entries" not in data

    def test_inspect_entries_lists_each_entry(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --entries lists each entry's own detail."""
        archive = temp_dir / "out.zip"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "zip", "-q"]
        )

        result = runner.invoke(app, ["inspect", str(archive), "--entries"])

        assert result.exit_code == EXIT_OK
        assert "f0.bin" in result.output

    def test_inspect_entries_json_lists_each_entry(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --entries --json includes a populated entries list."""
        import json

        archive = temp_dir / "out.zip"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "zip", "-q"]
        )

        result = runner.invoke(app, ["inspect", str(archive), "--entries", "--json"])

        assert result.exit_code == EXIT_OK
        data = json.loads(result.output)
        assert len(data["entries"]) == 4
        assert any("f0.bin" in entry["path"] for entry in data["entries"])


class TestExitCodes:
    """Test 0 success, 1 the operation failed, 2 the request could not be made."""

    def test_success_is_zero(self, payload: Path, temp_dir: Path) -> None:
        """Test that a completed job exits 0."""
        result = runner.invoke(
            app, ["compress", str(payload), "-o", str(temp_dir / "o.comp"), "-q"]
        )
        assert result.exit_code == EXIT_OK

    def test_missing_input_is_usage(self, temp_dir: Path) -> None:
        """Test that compressing a file that is not there is a usage error."""
        result = runner.invoke(app, ["compress", str(temp_dir / "nope.bin")])
        assert result.exit_code == EXIT_USAGE

    def test_decompressing_a_non_compresso_file_is_usage(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that the input is wrong, rather than the operation having failed."""
        result = runner.invoke(app, ["decompress", str(payload)])
        assert result.exit_code == EXIT_USAGE

    def test_inspecting_a_non_compresso_file_is_usage(self, payload: Path) -> None:
        """Test the same for inspect."""
        result = runner.invoke(app, ["inspect", str(payload)])
        assert result.exit_code == EXIT_USAGE

    def test_extracting_a_non_archive_is_usage(self, payload: Path) -> None:
        """Test the same for extract."""
        result = runner.invoke(app, ["extract", str(payload)])
        assert result.exit_code == EXIT_USAGE

    @pytest.mark.parametrize(
        "flags",
        [
            ["--overwrite", "--skip-existing"],
            ["--overwrite", "--error-on-conflict"],
            ["--skip-existing", "--error-on-conflict"],
        ],
    )
    def test_mutually_exclusive_flags_are_usage(
        self, source_tree: Path, temp_dir: Path, flags: list[str]
    ) -> None:
        """Test that combining any two overwrite-mode flags is contradictory."""
        archive = temp_dir / "out.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )

        result = runner.invoke(
            app,
            ["extract", str(archive), *flags, "-o", str(temp_dir / "d")],
        )
        assert result.exit_code == EXIT_USAGE
        assert "mutually exclusive" in result.output

    @pytest.mark.parametrize(
        "flags",
        [
            ["--overwrite", "--skip-existing"],
            ["--overwrite", "--error-on-conflict"],
            ["--skip-existing", "--error-on-conflict"],
        ],
    )
    def test_compress_mutually_exclusive_flags_are_usage(
        self, source_tree: Path, temp_dir: Path, flags: list[str]
    ) -> None:
        """Test that combining any two archive overwrite-mode flags is contradictory."""
        result = runner.invoke(
            app,
            [
                "compress",
                str(source_tree),
                "-o",
                str(temp_dir / "out.tar"),
                "-f",
                "tar",
                *flags,
            ],
        )
        assert result.exit_code == EXIT_USAGE
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
        result = runner.invoke(app, ["compress", str(payload), "-o", str(dest), *args])
        assert result.exit_code == EXIT_USAGE
        assert message in result.output
        assert not dest.exists()

    def test_out_of_range_archive_level_is_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that an archive's level is checked against the stage that compresses."""
        dest = temp_dir / "out.tar"
        result = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(dest), "-f", "tar", "-l", "5"]
        )
        assert result.exit_code == EXIT_USAGE
        assert "tar has no compression levels" in result.output
        assert not dest.exists()

    def test_bad_benchmark_level_is_usage(self, payload: Path) -> None:
        """Test that a level that is not a number is a usage error."""
        result = runner.invoke(app, ["benchmark", str(payload), "--levels", "nope"])
        assert result.exit_code == EXIT_USAGE

    def test_unknown_archive_format_is_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a format that cannot hold multiple entries is refused up front."""
        result = runner.invoke(
            app,
            ["compress", str(source_tree), "-o", str(temp_dir / "o.gz"), "-f", "gz"],
        )
        assert result.exit_code == EXIT_USAGE

    def test_recognised_but_unsupported_archive_names_itself(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a detected-but-unwritable container reports its own name."""
        dest = temp_dir / "o.7z"
        result = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(dest), "-f", "7z"]
        )
        assert result.exit_code == EXIT_FAILED
        assert "7z archives are recognised but not supported yet" in result.output
        assert not dest.exists()

    def test_unsupported_archive_suffix_is_not_written_as_zip(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that `-o x.7z` alone names 7z rather than writing a zip there."""
        dest = temp_dir / "o.7z"
        result = runner.invoke(app, ["compress", str(source_tree), "-o", str(dest)])
        assert result.exit_code == EXIT_FAILED
        assert "7z archives are recognised but not supported yet" in result.output
        assert not dest.exists()

    def test_failed_operation_is_one(self, source_tree: Path, temp_dir: Path) -> None:
        """Test that a job that starts and then fails exits 1, not 2."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(archive), "-f", "tar", "-q"]
        )
        runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])

        result = runner.invoke(
            app,
            ["extract", str(archive), "-o", str(dest), "--error-on-conflict", "-q"],
        )
        assert result.exit_code == EXIT_FAILED


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
        temp_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        command: list[str],
        expected: str,
    ) -> None:
        """Test that each command names itself rather than dying silently."""
        # Interrupt the first real call each command makes
        import compresso.cli.algos as algos_mod
        import compresso.cli.inspect as inspect_mod
        from compresso.frontend import api

        def interrupt(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(api.CompressionJob, "from_file", interrupt, raising=False)
        monkeypatch.setattr(inspect_mod, "inspect_file", interrupt)
        monkeypatch.setattr(algos_mod, "list_capabilities", interrupt)

        args = [*command]
        if command[0] != "list":
            args.append(str(payload))

        result = runner.invoke(app, args)

        assert result.exit_code == EXIT_CANCELLED
        assert expected in result.output


class TestSmartCompress:
    """Test `compress` picks the job from its inputs and the format asked for."""

    def test_one_file_becomes_a_compresso_container(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that with no format, a single file gets the .comp container."""
        result = runner.invoke(app, ["compress", str(payload), "-q"])

        assert result.exit_code == EXIT_OK
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
        result = runner.invoke(app, ["compress", str(payload), "-f", fmt, "-q"])

        assert result.exit_code == EXIT_OK
        assert payload.with_suffix(payload.suffix + suffix).exists()

    def test_standalone_output_is_a_real_gzip(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that the .gz written is the format, not merely the name."""
        import gzip

        output = temp_dir / "out.gz"
        runner.invoke(app, ["compress", str(payload), "-o", str(output), "-q"])

        with gzip.open(output, "rb") as f:
            assert f.read() == payload.read_bytes()

    def test_a_directory_becomes_an_archive(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a directory needs a container that holds many entries."""
        result = runner.invoke(app, ["compress", str(source_tree), "-q"])

        assert result.exit_code == EXIT_OK
        assert (source_tree.parent / f"{source_tree.name}.zip").exists()

    def test_several_files_become_an_archive(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that multiple files become an archive."""
        second = temp_dir / "second.bin"
        second.write_bytes(os.urandom(1024))
        output = temp_dir / "both.tar"

        result = runner.invoke(
            app, ["compress", str(payload), str(second), "-o", str(output), "-q"]
        )

        assert result.exit_code == EXIT_OK
        assert output.exists()

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

        result = runner.invoke(app, ["compress", str(payload), "-o", str(output), "-q"])

        assert result.exit_code == EXIT_OK
        assert output.read_bytes().startswith(expected_magic)

    def test_explicit_format_beats_the_output_name(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that -f overrides whatever the extension suggests."""
        output = temp_dir / "misleading.gz"

        runner.invoke(
            app, ["compress", str(payload), "-o", str(output), "-f", "zst", "-q"]
        )

        assert output.read_bytes().startswith(b"\x28\xb5\x2f\xfd")

    def test_tar_zst_beats_zst_in_the_name(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that the longest matching suffix wins, so out.tar.zst is an archive."""
        output = temp_dir / "out.tar.zst"

        result = runner.invoke(
            app, ["compress", str(source_tree), "-o", str(output), "-q"]
        )

        assert result.exit_code == EXIT_OK
        # Round-trips as an archive rather than as a lone compressed file
        dest = temp_dir / "back"
        assert (
            runner.invoke(
                app, ["extract", str(output), "-o", str(dest), "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert (dest / source_tree.name).is_dir()

    def test_single_file_format_refuses_several_inputs(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a .gz holds one file, so a directory cannot go into one."""
        result = runner.invoke(
            app,
            ["compress", str(source_tree), "-f", "gz", "-o", str(temp_dir / "o.gz")],
        )

        assert result.exit_code == EXIT_USAGE
        assert "single file" in result.output

    def test_unknown_format_is_refused(self, payload: Path) -> None:
        """Test that an unrecognised -f names neither container."""
        result = runner.invoke(app, ["compress", str(payload), "-f", "bogus"])

        assert result.exit_code == EXIT_USAGE


class TestSmartExtract:
    """Test `extract` picks the job from each file's own bytes."""

    @pytest.mark.parametrize("fmt", ["gz", "bz2", "xz", "zst", "lz4"])
    def test_standalone_round_trip(
        self, payload: Path, temp_dir: Path, fmt: str
    ) -> None:
        """Test every standalone container goes out and comes back."""
        packed = temp_dir / f"p.{fmt}"
        restored = temp_dir / f"back.{fmt}"

        runner.invoke(app, ["compress", str(payload), "-o", str(packed), "-q"])
        result = runner.invoke(app, ["extract", str(packed), "-o", str(restored), "-q"])

        assert result.exit_code == EXIT_OK
        assert restored.read_bytes() == payload.read_bytes()

    def test_dispatch_follows_the_bytes_not_the_name(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that a renamed .gz is still recognised as one."""
        packed = temp_dir / "p.gz"
        runner.invoke(app, ["compress", str(payload), "-o", str(packed), "-q"])

        disguised = temp_dir / "mystery.dat"
        disguised.write_bytes(packed.read_bytes())
        restored = temp_dir / "back.bin"

        result = runner.invoke(
            app, ["extract", str(disguised), "-o", str(restored), "-q"]
        )

        assert result.exit_code == EXIT_OK
        assert restored.read_bytes() == payload.read_bytes()

    def test_an_archive_named_like_anything_still_extracts(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a renamed archive still extracts."""
        packed = temp_dir / "p.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(packed), "-f", "tar", "-q"]
        )

        disguised = temp_dir / "riddle.bin"
        disguised.write_bytes(packed.read_bytes())
        dest = temp_dir / "out"

        result = runner.invoke(app, ["extract", str(disguised), "-o", str(dest), "-q"])

        assert result.exit_code == EXIT_OK
        assert (dest / source_tree.name).is_dir()

    def test_an_archive_unpacks_beside_itself(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that with no -o, entries land next to the archive, not in the cwd."""
        packed = temp_dir / "p.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(packed), "-f", "tar", "-q"]
        )

        # The sources are beside the archive, so they would be in the way
        for f in source_tree.iterdir():
            f.unlink()
        source_tree.rmdir()

        result = runner.invoke(app, ["extract", str(packed), "-q"])

        assert result.exit_code == EXIT_OK
        assert (temp_dir / source_tree.name).is_dir()
        assert not (Path.cwd() / source_tree.name).exists()

    def test_output_directory_receives_a_single_file(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that -o naming an existing directory puts the file inside it."""
        packed = temp_dir / "p.gz"
        runner.invoke(app, ["compress", str(payload), "-o", str(packed), "-q"])

        into = temp_dir / "into"
        into.mkdir()

        result = runner.invoke(app, ["extract", str(packed), "-o", str(into), "-q"])

        assert result.exit_code == EXIT_OK
        assert (into / "p").read_bytes() == payload.read_bytes()

    def test_several_inputs_in_one_go(self, payload: Path, temp_dir: Path) -> None:
        """Test that each input is dispatched on its own."""
        first = temp_dir / "a.gz"
        second = temp_dir / "b.zst"
        runner.invoke(app, ["compress", str(payload), "-o", str(first), "-q"])
        runner.invoke(app, ["compress", str(payload), "-o", str(second), "-q"])

        result = runner.invoke(app, ["extract", str(first), str(second), "-q"])

        assert result.exit_code == EXIT_OK
        assert (temp_dir / "a").read_bytes() == payload.read_bytes()
        assert (temp_dir / "b").read_bytes() == payload.read_bytes()

    def test_listing_a_single_file_container_is_refused(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that there is nothing to list in a container holding one file."""
        packed = temp_dir / "p.gz"
        runner.invoke(app, ["compress", str(payload), "-o", str(packed), "-q"])

        result = runner.invoke(app, ["extract", str(packed), "--list"])

        assert result.exit_code == EXIT_USAGE
        assert "nothing to list" in result.output


class TestMergedVerbCompatibility:
    """Test that the old names still work, and the old argument order says so."""

    @pytest.mark.parametrize("name", ["archive", "a", "ar"])
    def test_archive_names_reach_compress(
        self, source_tree: Path, temp_dir: Path, name: str
    ) -> None:
        """Test that `archive` is an alias of `compress`."""
        output = temp_dir / "out.tar.zst"

        result = runner.invoke(app, [name, str(source_tree), "-o", str(output), "-q"])

        assert result.exit_code == EXIT_OK
        assert output.exists()

    @pytest.mark.parametrize("name", ["decompress", "d", "decomp"])
    def test_decompress_names_reach_extract(
        self, payload: Path, temp_dir: Path, name: str
    ) -> None:
        """Test that `decompress` is an alias of `extract`."""
        packed = temp_dir / "p.comp"
        restored = temp_dir / "back.bin"
        runner.invoke(app, ["compress", str(payload), "-o", str(packed), "-q"])

        result = runner.invoke(app, [name, str(packed), "-o", str(restored), "-q"])

        assert result.exit_code == EXIT_OK
        assert restored.read_bytes() == payload.read_bytes()

    def test_decompress_now_handles_archives_too(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that the merged verb does not care which name was typed."""
        packed = temp_dir / "out.tar"
        runner.invoke(
            app, ["compress", str(source_tree), "-o", str(packed), "-f", "tar", "-q"]
        )

        result = runner.invoke(
            app, ["decompress", str(packed), "-o", str(temp_dir / "out"), "-q"]
        )

        assert result.exit_code == EXIT_OK

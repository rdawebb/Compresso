"""Tests for the Compresso CLI."""

from __future__ import annotations

import os
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

        Alphabetical imports would put `list` first and split the
        compress/decompress and archive/extract pairs.
        """
        output = runner.invoke(app, ["--help"]).output

        # Matched on the alias column, which is unique per row; a bare command
        # name also appears inside other commands' descriptions
        positions = [
            output.index(aliases)
            for aliases in [
                "(c, comp)",
                "(d, decomp)",
                "(a, ar)",
                "(x, ex)",
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
            app, ["archive", str(archive), str(source_tree), "-f", fmt]
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
            app, ["archive", str(archive), str(source_tree), "-f", "tar", "-q"]
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

    def test_extract_refuses_to_overwrite_by_default(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that extract refuses to overwrite by default."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["archive", str(archive), str(source_tree), "-f", "tar", "-q"]
        )

        assert (
            runner.invoke(
                app, ["extract", str(archive), "-o", str(dest), "-q"]
            ).exit_code
            == EXIT_OK
        )
        assert (
            runner.invoke(
                app, ["extract", str(archive), "-o", str(dest), "-q"]
            ).exit_code
            == EXIT_FAILED
        )

    @pytest.mark.parametrize("flag", ["--overwrite", "--skip-existing"])
    def test_extract_over_existing_with_a_flag(
        self, source_tree: Path, temp_dir: Path, flag: str
    ) -> None:
        """Test that either flag makes a repeat extraction succeed."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["archive", str(archive), str(source_tree), "-f", "tar", "-q"]
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

    def test_list_shows_backends(self) -> None:
        """Test that list names the compiled-in algorithms."""
        result = runner.invoke(app, ["list"])

        assert result.exit_code == EXIT_OK
        assert "zstd" in result.output


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

    def test_mutually_exclusive_flags_are_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that --overwrite with --skip-existing is contradictory."""
        archive = temp_dir / "out.tar"
        runner.invoke(
            app, ["archive", str(archive), str(source_tree), "-f", "tar", "-q"]
        )

        result = runner.invoke(
            app,
            [
                "extract",
                str(archive),
                "--overwrite",
                "--skip-existing",
                "-o",
                str(temp_dir / "d"),
            ],
        )
        assert result.exit_code == EXIT_USAGE
        assert "mutually exclusive" in result.output

    def test_bad_benchmark_level_is_usage(self, payload: Path) -> None:
        """Test that a level that is not a number is a usage error."""
        result = runner.invoke(app, ["benchmark", str(payload), "--levels", "nope"])
        assert result.exit_code == EXIT_USAGE

    def test_unknown_archive_format_is_usage(
        self, source_tree: Path, temp_dir: Path
    ) -> None:
        """Test that a format that cannot hold multiple entries is refused up front."""
        result = runner.invoke(
            app, ["archive", str(temp_dir / "o.gz"), str(source_tree), "-f", "gz"]
        )
        assert result.exit_code == EXIT_USAGE

    def test_failed_operation_is_one(self, source_tree: Path, temp_dir: Path) -> None:
        """Test that a job that starts and then fails exits 1, not 2."""
        archive = temp_dir / "out.tar"
        dest = temp_dir / "dest"
        runner.invoke(
            app, ["archive", str(archive), str(source_tree), "-f", "tar", "-q"]
        )
        runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])

        result = runner.invoke(app, ["extract", str(archive), "-o", str(dest), "-q"])
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
            (["decompress"], "Decompression cancelled"),
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
        monkeypatch.setattr(api.DecompressionJob, "from_file", interrupt, raising=False)
        monkeypatch.setattr(inspect_mod, "inspect_file", interrupt)
        monkeypatch.setattr(algos_mod, "list_capabilities", interrupt)

        args = [*command]
        if command[0] != "list":
            args.append(str(payload))

        result = runner.invoke(app, args)

        assert result.exit_code == EXIT_CANCELLED
        assert expected in result.output

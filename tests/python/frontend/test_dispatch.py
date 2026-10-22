"""Tests for the frontend dispatch rules."""

import gzip
import os
import zipfile
from pathlib import Path

import pytest

from compresso.frontend.api import CompressionJob, DecompressionJob
from compresso.frontend.archive_api import ArchiveJob, ExtractJob
from compresso.frontend.dispatch import (
    CompressRequest,
    ExtractRequest,
    default_archive_output,
    format_is_single_file,
    infer_format,
    is_archive_format,
    looks_like_archive,
    plan_compress,
    plan_extract,
    resolve_single_output,
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A directory holding one file."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("a")

    return root


class TestInferFormat:
    """Test reading a format off an output filename."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("x.tar.lz4", "tar.lz4"),
            ("x.TAR.ZST", "tar.zst"),
            ("x.tar", "tar"),
            ("x.zip", "zip"),
            ("x.7z", "7z"),
            ("x.gz", "gz"),
            ("x.zst", "zst"),
            ("x.comp", None),
            ("x.txt", None),
            ("x", None),
            ("x.foo.lz4", "lz4"),
        ],
    )
    def test_suffix(self, name: str, expected: str | None) -> None:
        """Test that the longest known suffix wins, case-insensitively."""
        assert infer_format(Path(name)) == expected

    def test_no_output(self) -> None:
        """Test that no output path infers no format."""
        assert infer_format(None) is None


class TestFormatKinds:
    """Test classifying a format as an archive or a single-file container."""

    @pytest.mark.parametrize(
        ("fmt", "archive", "single"),
        [
            ("zip", True, False),
            ("TAR.GZ", True, False),
            ("7z", True, False),
            ("gz", False, True),
            ("ZSTD", False, True),
            ("comp", False, False),
            ("nope", False, False),
            (None, False, False),
        ],
    )
    def test_kind(self, fmt: str | None, archive: bool, single: bool) -> None:
        """Test that `.comp` and unknown formats are neither kind."""
        assert is_archive_format(fmt) is archive
        assert format_is_single_file(fmt) is single


class TestLooksLikeArchive:
    """Test detecting an archive from its bytes rather than its name."""

    def test_zip_under_any_name(self, tmp_path: Path) -> None:
        """Test that a zip is recognised without a `.zip` suffix."""
        path = tmp_path / "data.bin"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.txt", "a")

        assert looks_like_archive(path)

    def test_single_file_container(self, tmp_path: Path) -> None:
        """Test that a gzip stream is not an archive, even named `.zip`."""
        path = tmp_path / "data.zip"
        path.write_bytes(gzip.compress(b"a"))

        assert not looks_like_archive(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test that a missing file is not an archive rather than an error."""
        assert not looks_like_archive(tmp_path / "missing.zip")


class TestDefaultArchiveOutput:
    """Test naming an archive the caller did not name."""

    def test_beside_the_input(self, tmp_path: Path) -> None:
        """Test that the archive is named after the input, next to it."""
        src = tmp_path / "src"
        src.mkdir()

        assert default_archive_output(src, "tar.zst") == tmp_path / "src.tar.zst"

    @pytest.mark.parametrize("name", [".", f"..{os.sep}src{os.sep}"])
    def test_trailing_separator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        """Test that `.` and a trailing separator still name the directory."""
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.chdir(src)

        assert (
            default_archive_output(Path(name), "zip") == tmp_path.resolve() / "src.zip"
        )


class TestResolveSingleOutput:
    """Test where `-o` sends a single-file job."""

    def test_no_output_keeps_default(self, tmp_path: Path) -> None:
        """Test that without `-o` the default path is used."""
        default = tmp_path / "x.gz"

        assert resolve_single_output(None, default) == default

    def test_existing_directory_receives_the_file(self, tmp_path: Path) -> None:
        """Test that an existing directory gets the default filename inside it."""
        out = tmp_path / "out"
        out.mkdir()

        assert resolve_single_output(out, tmp_path / "x.gz") == out / "x.gz"

    def test_anything_else_is_the_file(self, tmp_path: Path) -> None:
        """Test that any other path is taken as the output file itself."""
        out = tmp_path / "out" / "y.gz"

        assert resolve_single_output(out, tmp_path / "x.gz") == out


class TestPlanCompress:
    """Test `compress` picks the job from its inputs and the format asked for."""

    def test_one_file_becomes_a_compresso_container(self, small_file: Path) -> None:
        """Test that one file with no format defaults to a `.comp` beside it."""
        job = plan_compress(CompressRequest([small_file]))

        assert isinstance(job, CompressionJob)
        assert job.plan.options.format is None
        assert job.plan.dest == small_file.with_name("small.txt.comp")

    @pytest.mark.parametrize("fmt", ["gz", "bz2", "xz", "zst", "lz4", "sz"])
    def test_one_file_into_a_standalone_container(
        self, small_file: Path, fmt: str
    ) -> None:
        """Test that each single-file format compresses one file beside it."""
        job = plan_compress(CompressRequest([small_file], format=fmt))

        assert isinstance(job, CompressionJob)
        assert job.plan.can_compress
        assert job.plan.dest == small_file.with_name(f"small.txt.{fmt}")

    def test_a_directory_becomes_an_archive(self, tree: Path) -> None:
        """Test that a directory with no format defaults to a zip beside it."""
        job = plan_compress(CompressRequest([tree]))

        assert isinstance(job, ArchiveJob)
        assert job.plan.options.format == "zip"
        assert job.plan.output == tree.with_name("tree.zip")

    def test_several_files_become_an_archive(
        self, small_file: Path, tmp_path: Path
    ) -> None:
        """Test that several files are archived in the output's format."""
        second = tmp_path / "second.txt"
        second.write_text("b")
        output = tmp_path / "both.tar"

        job = plan_compress(CompressRequest([small_file, second], output=output))

        assert isinstance(job, ArchiveJob)
        assert job.plan.options.format == "tar"
        assert job.plan.output == output

    @pytest.mark.parametrize(
        ("name", "kind", "fmt"),
        [
            ("out.gz", CompressionJob, "gz"),
            ("out.zst", CompressionJob, "zst"),
            ("out.zip", ArchiveJob, "zip"),
        ],
    )
    def test_format_inferred_from_the_output_name(
        self, small_file: Path, tmp_path: Path, name: str, kind: type, fmt: str
    ) -> None:
        """Test that the output's suffix picks both the format and the job kind."""
        output = tmp_path / name

        job = plan_compress(CompressRequest([small_file], output=output))

        assert isinstance(job, kind)
        assert job.plan.options.format == fmt

    def test_explicit_format_beats_the_output_name(
        self, small_file: Path, tmp_path: Path
    ) -> None:
        """Test that `--format` wins over the output's suffix, keeping its name."""
        output = tmp_path / "misleading.gz"

        job = plan_compress(CompressRequest([small_file], output=output, format="zst"))

        assert isinstance(job, CompressionJob)
        assert job.plan.options.format == "zst"
        assert job.plan.dest == output

    @pytest.mark.parametrize(
        "name", ["out.tar.zst", "out.tar.lz4", "out.tlz4", "out.tar.sz"]
    )
    def test_tar_inside_a_codec_is_an_archive(
        self, tree: Path, tmp_path: Path, name: str
    ) -> None:
        """Test that compressed tar suffixes, short forms included, archive."""
        job = plan_compress(CompressRequest([tree], output=tmp_path / name))

        assert isinstance(job, ArchiveJob)
        assert job.plan.can_run
        assert job.plan.options.format == name.removeprefix("out.")

    def test_existing_directory_receives_the_file(
        self, small_file: Path, tmp_path: Path
    ) -> None:
        """Test that an existing `-o` directory gets the default filename."""
        into = tmp_path / "into"
        into.mkdir()

        job = plan_compress(CompressRequest([small_file], output=into, format="gz"))

        assert isinstance(job, CompressionJob)
        assert job.plan.dest == into / "small.txt.gz"

    def test_single_file_format_refuses_several_inputs(self, tree: Path) -> None:
        """Test that a single-file format on a directory errors, suggesting tar."""
        with pytest.raises(ValueError, match="tar.gz"):
            plan_compress(CompressRequest([tree], format="gz"))

    def test_unknown_format_cannot_run(self, small_file: Path) -> None:
        """Test that an unknown format plans a job that refuses to run."""
        job = plan_compress(CompressRequest([small_file], format="bogus"))

        assert isinstance(job, CompressionJob)
        assert not job.plan.can_compress


class TestPlanExtract:
    """Test `extract` picks each input's job from its own bytes."""

    def test_dispatch_follows_the_bytes_not_the_name(self, tmp_path: Path) -> None:
        """Test that a gzip stream with an unknown suffix still decompresses."""
        disguised = tmp_path / "mystery.dat"
        disguised.write_bytes(gzip.compress(b"a"))
        restored = tmp_path / "back.bin"

        [job] = plan_extract(ExtractRequest([disguised], output=restored))

        assert isinstance(job, DecompressionJob)
        assert job.plan.format == "gzip"
        assert job.plan.dest == restored

    def test_an_archive_named_like_anything_still_extracts(
        self, tmp_path: Path
    ) -> None:
        """Test that a zip without a `.zip` suffix plans a runnable extraction."""
        disguised = tmp_path / "riddle.bin"
        with zipfile.ZipFile(disguised, "w") as zf:
            zf.writestr("a.txt", "a")
        dest = tmp_path / "out"

        [job] = plan_extract(ExtractRequest([disguised], output=dest))

        assert isinstance(job, ExtractJob)
        assert job.plan.can_run
        assert job.plan.output_dir == dest

    def test_an_archive_unpacks_beside_itself(self, tmp_path: Path) -> None:
        """Test that without `-o` an archive extracts into its own directory."""
        archive = tmp_path / "x.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a.txt", "a")

        [job] = plan_extract(ExtractRequest([archive]))

        assert isinstance(job, ExtractJob)
        assert job.plan.output_dir == tmp_path

    def test_output_directory_receives_a_single_file(self, tmp_path: Path) -> None:
        """Test that an existing `-o` directory gets the suffix-stripped name."""
        packed = tmp_path / "p.gz"
        packed.write_bytes(gzip.compress(b"a"))
        into = tmp_path / "into"
        into.mkdir()

        [job] = plan_extract(ExtractRequest([packed], output=into))

        assert isinstance(job, DecompressionJob)
        assert job.plan.dest == into / "p"

    def test_each_input_is_dispatched_on_its_own(self, tmp_path: Path) -> None:
        """Test that a mixed list plans one job per input, in order."""
        packed = tmp_path / "a.gz"
        packed.write_bytes(gzip.compress(b"a"))
        archive = tmp_path / "b.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("b.txt", "b")

        first, second = plan_extract(ExtractRequest([packed, archive]))

        assert isinstance(first, DecompressionJob)
        assert first.plan.dest == tmp_path / "a"
        assert isinstance(second, ExtractJob)

"""Tests for the frontend archive API module."""

import sys
import tarfile
import unicodedata
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from compresso import ExtractionPolicyError
from compresso.frontend._job import JobResult
from compresso.frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractOptions,
    ExtractPlan,
    OverwriteMode,
    plan_archive,
    plan_extraction,
)

from ..helpers import dir_entry, file_entry, link_entry, make_tar, renamed, umask


class TestArchiveOptions:
    """Test the ArchiveOptions dataclass."""

    def test_archive_options_default(self) -> None:
        """Test creating ArchiveOptions with defaults."""
        opts = ArchiveOptions()

        assert opts.format == "tar.zst"
        assert opts.compression_level is None
        assert opts.overwrite is OverwriteMode.RENAME


class TestPlanArchive:
    """Test the plan_archive planner."""

    def test_plan_archive_valid(self, sample_text_file: Path, tmp_path: Path) -> None:
        """Test that a plan over existing sources can run and sums input size."""
        out = tmp_path / "out.tar.zst"
        plan = plan_archive([sample_text_file], out)

        assert isinstance(plan, ArchivePlan)
        assert plan.can_run is True
        assert plan.reason_if_unavailable is None
        assert plan.entry_count == 1
        assert plan.total_input_size == sample_text_file.stat().st_size

    def test_plan_archive_no_sources(self, tmp_path: Path) -> None:
        """Test that a plan with no sources cannot run."""
        plan = plan_archive([], tmp_path / "out.tar.zst")

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None

    def test_plan_archive_missing_source(self, tmp_path: Path) -> None:
        """Test that a plan referencing a missing source cannot run."""
        plan = plan_archive([tmp_path / "nope.txt"], tmp_path / "out.tar.zst")

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None
        assert "does not exist" in plan.reason_if_unavailable

    def test_plan_archive_non_archive_format(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a single-file codec format cannot be used to build an archive."""
        opts = ArchiveOptions(format="gz")
        plan = plan_archive([sample_text_file], tmp_path / "out.gz", opts)

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None
        assert "does not support archives" in plan.reason_if_unavailable

    @pytest.mark.parametrize(
        "fmt,level,match",
        [
            ("tar", 1, "tar has no compression levels"),
            ("zip", 10, "zip compression level 10"),
            ("tar.zst", 23, "zstd compression level 23"),
        ],
    )
    def test_plan_archive_out_of_range_level(
        self, sample_text_file: Path, tmp_path: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that the stage that compresses decides which levels are valid."""
        opts = ArchiveOptions(format=fmt, compression_level=level)
        plan = plan_archive([sample_text_file], tmp_path / f"out.{fmt}", opts)

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None
        assert match in plan.reason_if_unavailable

    def test_plan_archive_codec_stage_level(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that tar.zst takes zstd's levels even though tar has none."""
        opts = ArchiveOptions(format="tar.zst", compression_level=19)
        assert plan_archive([sample_text_file], tmp_path / "o.tar.zst", opts).can_run


class TestArchiveJob:
    """Test the ArchiveJob class."""

    def test_from_paths_builds_plan(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that from_paths runs the planner and exposes the plan."""
        job = ArchiveJob.from_paths([sample_text_file], tmp_path / "out.tar.zst")

        assert isinstance(job.plan, ArchivePlan)
        assert job.plan.can_run is True

    def test_run_on_unavailable_plan_returns_failed_result(
        self, tmp_path: Path
    ) -> None:
        """Test that run() never raises: an unrunnable plan yields a failed JobResult."""
        job = ArchiveJob.from_paths([], tmp_path / "out.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False
        assert result.error is not None

    def test_level_zero_is_not_the_default(self, tmp_path: Path) -> None:
        """Test that level 0 reaches the codec rather than reading as "unset"."""
        source = tmp_path / "text.txt"
        source.write_text("a compressible line of text\n" * 4000)

        def archive_size(level: int | None) -> int:
            out = tmp_path / f"level_{level}.tar.gz"
            opts = ArchiveOptions(format="tar.gz", compression_level=level)
            assert ArchiveJob.from_paths([source], out, opts).run().ok
            return out.stat().st_size

        # gzip level 0 stores the data, so it is far larger than the default
        assert archive_size(0) > archive_size(None) * 10


class TestArchiveOverwrite:
    """Test the four `overwrite` modes against an existing destination archive."""

    @staticmethod
    def _source_and_stale_output(tmp_path: Path) -> tuple[Path, Path]:
        """Build a source file and a stale file already at the archive's path.

        Args:
            tmp_path: Directory to build both in.

        Returns:
            The source path, and the archive path already occupied by an
            unrelated file.
        """
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = tmp_path / "out.tar"
        archive_path.write_bytes(b"stale")

        return source, archive_path

    def test_renames_existing_archive_by_default(self, tmp_path: Path) -> None:
        """Test that the default policy renames rather than overwriting."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        job = ArchiveJob.from_paths([source], archive_path)

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() == b"stale"
        sibling = tmp_path / renamed("out.tar")
        assert job.plan.output == sibling
        assert sibling.is_file()
        assert sibling.read_bytes() != b"stale"

    def test_rename_keeps_a_compound_extension_intact(self, tmp_path: Path) -> None:
        """Test that "out.tar.zst" renames to "out 2.tar.zst", not "out.tar 2.zst".

        `create_archive` writes a codec format's output only at the very end,
        through a separate temp-file stage - a case the plain single-extension
        rename doesn't exercise.
        """
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = tmp_path / "out.tar.zst"
        archive_path.write_bytes(b"stale")

        job = ArchiveJob.from_paths(
            [source], archive_path, options=ArchiveOptions(format="tar.zst")
        )
        result = job.run()

        assert result.ok, result.error
        sibling = tmp_path / renamed("out.tar.zst")
        assert job.plan.output == sibling
        assert sibling.is_file()

    def test_rename_keeps_an_uppercase_compound_extension_intact(
        self, tmp_path: Path
    ) -> None:
        """Test that "OUT.TAR.GZ" renames as "out.tar.gz" does."""
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = tmp_path / "OUT.TAR.GZ"
        archive_path.write_bytes(b"stale")

        job = ArchiveJob.from_paths(
            [source], archive_path, options=ArchiveOptions(format="tar.gz")
        )
        result = job.run()

        assert result.ok, result.error
        assert job.plan.output == tmp_path / renamed("OUT.TAR.GZ")

    def test_rename_increments_through_repeated_conflicts(self, tmp_path: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        options = ArchiveOptions(format="tar", overwrite=OverwriteMode.RENAME)

        assert ArchiveJob.from_paths([source], archive_path, options).run().ok
        result = ArchiveJob.from_paths([source], archive_path, options).run()

        assert result.ok, result.error
        assert (tmp_path / renamed("out.tar")).exists()
        assert (tmp_path / renamed("out.tar", 3)).exists()

    def test_error_mode_refuses_existing_archive(self, tmp_path: Path) -> None:
        """Test that explicit `error` refuses to touch an existing archive."""
        source, archive_path = self._source_and_stale_output(tmp_path)

        result = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.ERROR),
        ).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert archive_path.read_bytes() == b"stale"

    def test_skip_leaves_the_existing_archive(self, tmp_path: Path) -> None:
        """Test that `skip` leaves the stale archive untouched and still succeeds."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        job = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.SKIP),
        )

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() == b"stale"
        assert job.plan.output == archive_path

    def test_overwrite_replaces_the_existing_archive(self, tmp_path: Path) -> None:
        """Test that `overwrite` replaces the stale archive in place."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        job = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.OVERWRITE),
        )

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() != b"stale"
        assert job.plan.output == archive_path


class TestExtractJob:
    """Test the ExtractJob class."""

    def test_from_archive_missing_returns_unavailable_plan(
        self, tmp_path: Path
    ) -> None:
        """Test that a missing archive produces a plan that cannot run."""
        job = ExtractJob.from_archive(tmp_path / "missing.tar.zst")

        assert isinstance(job.plan, ExtractPlan)
        assert job.plan.can_run is False

    def test_run_on_unavailable_plan_returns_failed_result(
        self, tmp_path: Path
    ) -> None:
        """Test that run() never raises on a missing archive."""
        job = ExtractJob.from_archive(tmp_path / "missing.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False

    def test_output_dir_defaults_to_the_archives_own_directory(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test that omitting output_dir plans beside the archive, not in the cwd."""
        nested: Path = tmp_path / "nested"
        nested.mkdir()
        monkeypatch.chdir(tmp_path)

        plan = ExtractJob.from_archive(nested / "missing.tar.zst").plan

        assert plan.output_dir == nested.resolve()

    def test_output_dir_default_ignores_a_relative_archive_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test that a relative archive path still resolves to a real directory."""
        monkeypatch.chdir(tmp_path)

        plan = ExtractJob.from_archive("missing.tar.zst").plan

        assert plan.output_dir == tmp_path.resolve()


class TestArchiveRoundTrip:
    """Test end-to-end archive -> extract round-trip."""

    def test_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        """Test that archiving then extracting restores the original file contents.

        Archives store source paths verbatim and extraction refuses absolute
        paths, so archive from within the working directory using a relative
        source name (matching real CLI usage).
        """
        monkeypatch.chdir(tmp_path)
        src = Path("data.txt")
        src.write_bytes(b"hello compresso" * 100)
        archive_path = Path("bundle.tar.zst")

        archive_result = ArchiveJob.from_paths([src], archive_path).run()
        assert archive_result.ok, archive_result.error
        assert archive_path.is_file()

        # list_contents surfaces real ArchiveEntry objects.
        extract_job = ExtractJob.from_archive(archive_path, tmp_path / "restore")
        entries = extract_job.list_contents()
        assert entries
        assert all(isinstance(e, ArchiveEntry) for e in entries)

        extract_result = extract_job.run()
        assert extract_result.ok, extract_result.error

        restored = tmp_path / "restore" / src.name
        assert restored.read_bytes() == src.read_bytes()

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_plan_entries_carry_sizes(
        self, tmp_path: Path, monkeypatch, fmt: str
    ) -> None:
        """Test that planned entries report each file's uncompressed size.

        The CLI sums these to decide whether to show a progress bar.
        """
        monkeypatch.chdir(tmp_path)
        tree = Path("sized")
        tree.mkdir()
        (tree / "a.bin").write_bytes(b"a" * 1234)
        (tree / "b.bin").write_bytes(b"b" * 56789)
        archive_path = Path(f"sized.{fmt}")

        options = ArchiveOptions(format=fmt)
        assert ArchiveJob.from_paths([tree], archive_path, options=options).run().ok

        entries = {
            e.path.rstrip("/"): e
            for e in ExtractJob.from_archive(archive_path).plan.entries
        }
        assert entries["sized/a.bin"].size == 1234
        assert entries["sized/b.bin"].size == 56789
        assert not entries["sized/a.bin"].is_dir
        assert entries["sized"].size == 0
        assert entries["sized"].is_dir

    def test_directory_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        """Test that archiving a directory preserves its nested structure on extraction."""
        monkeypatch.chdir(tmp_path)
        src = Path("tree")
        (src / "deep").mkdir(parents=True)
        (src / "top.txt").write_bytes(b"top level")
        (src / "deep" / "leaf.txt").write_bytes(b"leaf content")
        archive_path = Path("tree.tar.zst")

        assert ArchiveJob.from_paths([src], archive_path).run().ok

        names = {e.path for e in ExtractJob.from_archive(archive_path).list_contents()}
        assert "tree/top.txt" in names
        assert "tree/deep/leaf.txt" in names

        extract_result = ExtractJob.from_archive(archive_path, tmp_path / "out").run()
        assert extract_result.ok, extract_result.error

        assert (tmp_path / "out" / "tree" / "top.txt").read_bytes() == b"top level"
        assert (
            tmp_path / "out" / "tree" / "deep" / "leaf.txt"
        ).read_bytes() == b"leaf content"

    def test_non_ascii_names_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        """Test that non-ASCII names survive as both a source path and an entry name.

        Names are compared after NFC normalisation: macOS stores them decomposed,
        so a name read back off the filesystem is not byte-identical to the
        composed literal written here.
        """
        monkeypatch.chdir(tmp_path)
        src = Path("données")
        src.mkdir()
        (src / "café.txt").write_bytes(b"contenu")
        (src / "日本語.txt").write_bytes(b"content")
        archive_path = Path("archivé.tar.zst")

        assert ArchiveJob.from_paths([src], archive_path).run().ok

        names = {
            unicodedata.normalize("NFC", e.path)
            for e in ExtractJob.from_archive(archive_path).list_contents()
        }
        assert "données/café.txt" in names
        assert "données/日本語.txt" in names

        out_dir = tmp_path / "sortie"
        extract_result = ExtractJob.from_archive(archive_path, out_dir).run()
        assert extract_result.ok, extract_result.error

        assert (out_dir / "données" / "café.txt").read_bytes() == b"contenu"
        assert (out_dir / "données" / "日本語.txt").read_bytes() == b"content"

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_absolute_source_directory_round_trip(
        self, tmp_path: Path, fmt: str
    ) -> None:
        """Test that an absolute source directory is stored relative to its parent."""
        src = tmp_path / "tree"
        (src / "deep").mkdir(parents=True)
        (src / "top.txt").write_bytes(b"top level")
        (src / "deep" / "leaf.txt").write_bytes(b"leaf content")
        archive_path = tmp_path / f"tree.{fmt}"

        options = ArchiveOptions(format=fmt)
        result = ArchiveJob.from_paths([src.resolve()], archive_path, options).run()
        assert result.ok, result.error

        names = {e.path for e in ExtractJob.from_archive(archive_path).list_contents()}
        assert "tree/top.txt" in names
        assert "tree/deep/leaf.txt" in names
        for name in names:
            assert not name.startswith(("/", "\\")), name
            assert "\\" not in name, name

        out_dir = tmp_path / "out"
        extract_result = ExtractJob.from_archive(archive_path, out_dir).run()
        assert extract_result.ok, extract_result.error

        assert (out_dir / "tree" / "top.txt").read_bytes() == b"top level"
        assert (out_dir / "tree" / "deep" / "leaf.txt").read_bytes() == b"leaf content"


@pytest.mark.usefixtures("needs_symlinks")
class TestArchivingSymlinks:
    """Test that a symlink is archived as a symlink, not as what it points at.

    Written against an uncompressed tar, checked both through `tarfile` and
    through the entries an extraction plan lists.
    """

    def test_symlink_is_stored_as_a_link(self, tmp_path: Path, monkeypatch) -> None:
        """Test that a symlink becomes a link entry rather than a copy of its target."""
        monkeypatch.chdir(tmp_path)
        src = Path("tree")
        src.mkdir()
        (src / "real.txt").write_bytes(b"real content")
        (src / "alias.txt").symlink_to("real.txt")

        archive_path = Path("tree.tar")
        options = ArchiveOptions(format="tar")
        assert ArchiveJob.from_paths([src], archive_path, options).run().ok

        with tarfile.open(archive_path) as tar:
            members = {m.name: m for m in tar}
        assert members["tree/alias.txt"].issym()
        assert members["tree/alias.txt"].linkname == "real.txt"
        assert members["tree/real.txt"].isreg()

        entries = {
            e.path: e for e in ExtractJob.from_archive(archive_path).plan.entries
        }
        assert entries["tree/alias.txt"].is_symlink
        assert entries["tree/alias.txt"].link_target == "real.txt"
        assert not entries["tree/real.txt"].is_symlink
        assert entries["tree/real.txt"].link_target is None

    def test_symlink_loop_does_not_recurse(self, tmp_path: Path, monkeypatch) -> None:
        """Test that a symlink pointing at its own parent terminates the walk."""
        monkeypatch.chdir(tmp_path)
        src = Path("tree")
        src.mkdir()
        (src / "a.txt").write_bytes(b"content")
        (src / "loop").symlink_to("..", target_is_directory=True)

        archive_path = Path("loop.tar")
        options = ArchiveOptions(format="tar")
        result = ArchiveJob.from_paths([src], archive_path, options).run()
        assert result.ok, result.error

        with tarfile.open(archive_path) as tar:
            names = sorted(m.name for m in tar)
        assert names == ["tree", "tree/a.txt", "tree/loop"]


class TestExtractionRefusesDeniedEntryTypes:
    """Test that the default policy refuses symlink entries."""

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        """Test that a policy refusal comes back as a failed result, not a raise."""
        archive_path = tmp_path / "typed.tar"
        make_tar(archive_path, link_entry("entry", "target", kind=tarfile.SYMTYPE))

        result = ExtractJob.from_archive(archive_path, tmp_path / "out").run()

        assert result.ok is False
        assert isinstance(result.error, ExtractionPolicyError)
        assert "contains symlink, but policy denies it" in str(result.error)

    def test_symlink_mode_reaches_the_policy(self, tmp_path: Path) -> None:
        """Test that `symlinks`, given as a plain string, is passed through."""
        archive_path = tmp_path / "link.tar"
        make_tar(archive_path, link_entry("entry", "target", kind=tarfile.SYMTYPE))
        out = tmp_path / "out"

        options = ExtractOptions(symlinks="file")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        result = ExtractJob.from_archive(archive_path, out, options=options).run()

        assert result.ok is True
        assert (out / "entry").read_bytes() == b"target"

    def test_zip_symlink_becomes_a_file_by_default(self, tmp_path: Path) -> None:
        """Test that leaving `symlinks` unset takes zip's own default."""
        archive_path = tmp_path / "link.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            info = zipfile.ZipInfo("entry")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, b"target")
        out = tmp_path / "out"

        result = ExtractJob.from_archive(archive_path, out).run()

        assert result.ok is True
        assert (out / "entry").read_bytes() == b"target"

    def test_unknown_symlink_mode_is_unavailable(self, tmp_path: Path) -> None:
        """Test that a misspelt mode is refused when planning, naming the field."""
        options = ExtractOptions(symlinks="follow")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        plan = plan_extraction(tmp_path / "a.tar", tmp_path, options=options)

        assert plan.can_run is False
        assert "Unknown symlinks mode: 'follow'" in (plan.reason_if_unavailable or "")


class TestExtractionOverwrite:
    """Test the four `overwrite` modes against an existing destination."""

    @staticmethod
    def _archive_and_stale_output(tmp_path: Path) -> tuple[Path, Path]:
        """Build a one-entry archive and an output dir already holding that name.

        Args:
            tmp_path: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = tmp_path / "one.tar"
        make_tar(archive_path, file_entry("a.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "a.txt").write_bytes(b"original")

        return archive_path, out_dir

    def test_renames_existing_file_by_default(self, tmp_path: Path) -> None:
        """Test that the default policy renames rather than refusing."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"original"
        sibling = out_dir / renamed("a.txt")
        assert sibling.read_bytes() == b"x"

    def test_rename_increments_through_repeated_conflicts(self, tmp_path: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)
        options = ExtractOptions(overwrite=OverwriteMode.RENAME)

        assert ExtractJob.from_archive(archive_path, out_dir, options=options).run().ok
        result = ExtractJob.from_archive(archive_path, out_dir, options=options).run()

        assert result.ok, result.error
        assert (out_dir / renamed("a.txt")).exists()
        assert (out_dir / renamed("a.txt", 3)).exists()

    def test_error_mode_still_refuses_on_request(self, tmp_path: Path) -> None:
        """Test that explicit `error` reproduces the old refuse-by-default behavior."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.ERROR)
        ).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_skip_leaves_the_existing_file(self, tmp_path: Path) -> None:
        """Test that `skip` succeeds without replacing what is already there."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.SKIP)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_overwrite_replaces_the_existing_file(self, tmp_path: Path) -> None:
        """Test that `overwrite` replaces the file's contents."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.OVERWRITE),
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"x"

    @staticmethod
    def _archive_with_dir_and_blocking_file(tmp_path: Path) -> tuple[Path, Path]:
        """Build a one-directory-entry archive and a same-named file blocking it.

        Args:
            tmp_path: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        return archive_path, out_dir

    def test_dir_clash_with_a_file_renames_by_default(self, tmp_path: Path) -> None:
        """Test that the default policy renames the directory rather than refusing."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(tmp_path)

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "d").read_bytes() == b"blocking file"
        sibling = out_dir / renamed("d")
        assert sibling.is_dir()

    def test_dir_clash_with_a_file_renames_nested_entries_too(
        self, tmp_path: Path
    ) -> None:
        """Test that entries nested under the renamed directory follow it."""
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"), file_entry("d/inside.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        assert (sibling / "inside.txt").read_bytes() == b"x"
        # The original "d" is untouched, and nothing was created back under it
        assert (out_dir / "d").read_bytes() == b"blocking file"

    def test_doubly_nested_dir_clash_composes_with_parent_rename(
        self, tmp_path: Path
    ) -> None:
        """Test that a second clash below an already-renamed ancestor composes."""
        archive_path = tmp_path / "dir.tar"
        make_tar(
            archive_path,
            dir_entry("a"),
            file_entry(f"{renamed('a')}/b"),
            dir_entry("a/b"),
            file_entry("a/b/inside.txt"),
        )

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "a").write_bytes(b"blocks a")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        renamed_a = out_dir / renamed("a")
        assert (renamed_a / "b").read_bytes() == b"x"  # the unrelated entry
        renamed_b = renamed_a / renamed("b")
        assert renamed_b.is_dir()
        assert (renamed_b / "inside.txt").read_bytes() == b"x"
        # Nothing was planted back under the archive's own, un-renamed "a/b"
        assert not (out_dir / "a" / "b").exists()

    def test_dir_clash_with_an_existing_dir_renames_by_default(
        self, tmp_path: Path
    ) -> None:
        """Test that re-extracting over an existing directory renames the
        directory itself rather than merging into it."""
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"), file_entry("d/inside.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").mkdir()
        (out_dir / "d" / "inside.txt").write_bytes(b"original")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        # The original directory and its contents are untouched
        assert (out_dir / "d" / "inside.txt").read_bytes() == b"original"

        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        # The nested file lands under the renamed directory with its own
        # name unchanged - not doubled up or given its own " 2" suffix
        assert (sibling / "inside.txt").read_bytes() == b"x"

    def test_dir_clash_with_an_existing_dir_renames_subdirs_too(
        self, tmp_path: Path
    ) -> None:
        """Test that subdirectories nested under a clashing directory follow
        the rename instead of being merged into the pre-existing tree, which
        would otherwise leave the files beneath them doubled up."""
        archive_path = tmp_path / "dir.tar"
        make_tar(
            archive_path,
            dir_entry("d"),
            dir_entry("d/sub"),
            file_entry("d/sub/inside.txt"),
        )

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d" / "sub").mkdir(parents=True)
        (out_dir / "d" / "sub" / "inside.txt").write_bytes(b"original")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        # The original tree is untouched - no doubled-up file inside it
        assert sorted(p.name for p in (out_dir / "d").iterdir()) == ["sub"]
        assert sorted(p.name for p in (out_dir / "d" / "sub").iterdir()) == [
            "inside.txt"
        ]
        assert (out_dir / "d" / "sub" / "inside.txt").read_bytes() == b"original"

        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        assert sorted(p.name for p in sibling.iterdir()) == ["sub"]
        assert (sibling / "sub" / "inside.txt").read_bytes() == b"x"

    def test_dir_clash_with_a_file_errors_in_error_mode(self, tmp_path: Path) -> None:
        """Test that explicit `error` also refuses, not the old silent no-op."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(tmp_path)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.ERROR),
        ).run()

        assert result.ok is False
        assert "already exists" in str(result.error)

    def test_dir_clash_with_a_file_is_skipped_in_skip_mode(
        self, tmp_path: Path
    ) -> None:
        """Test that `skip` leaves the blocking file untouched."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(tmp_path)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.SKIP)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "d").is_file()
        assert (out_dir / "d").read_bytes() == b"blocking file"

    def test_dir_clash_with_a_file_is_replaced_in_overwrite_mode(
        self, tmp_path: Path
    ) -> None:
        """Test that `overwrite` replaces the blocking file with the directory."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(tmp_path)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.OVERWRITE),
        ).run()

        assert result.ok, result.error
        assert (out_dir / "d").is_dir()


def _archive_plan(tmp_path: Path, overwrite: str) -> ArchivePlan:
    """Plan archiving one file with `overwrite` given as a plain string.

    Args:
        tmp_path: Directory to build the source in.
        overwrite: The overwrite mode, deliberately not an OverwriteMode.

    Returns:
        The archive plan.
    """
    source = tmp_path / "a.txt"
    source.write_bytes(b"hello compresso")
    options = ArchiveOptions(format="tar", overwrite=overwrite)  # ty: ignore

    return ArchiveJob.from_paths([source], tmp_path / "out.tar", options).plan


def _extract_plan(tmp_path: Path, overwrite: str) -> ExtractPlan:
    """Plan extracting a one-entry tar with `overwrite` given as a plain string.

    Args:
        tmp_path: Directory to build the archive in.
        overwrite: The overwrite mode, deliberately not an OverwriteMode.

    Returns:
        The extraction plan.
    """
    archive_path = tmp_path / "one.tar"
    make_tar(archive_path, file_entry("a.txt"))
    options = ExtractOptions(overwrite=overwrite)  # ty: ignore

    return ExtractJob.from_archive(archive_path, tmp_path / "out", options=options).plan


PLANNERS = pytest.mark.parametrize(
    "plan_with", [_archive_plan, _extract_plan], ids=["archive", "extract"]
)


class TestOverwriteModeNames:
    """Test how both archive jobs take an overwrite mode given by name."""

    @PLANNERS
    def test_plain_string_mode_is_normalised(
        self,
        tmp_path: Path,
        plan_with: Callable[[Path, str], ArchivePlan | ExtractPlan],
    ) -> None:
        """Test that a mode given as a plain string still plans as the enum."""
        plan = plan_with(tmp_path, "skip")

        assert plan.can_run is True
        assert plan.options.overwrite is OverwriteMode.SKIP

    @PLANNERS
    def test_unknown_mode_is_an_unavailable_plan(
        self,
        tmp_path: Path,
        plan_with: Callable[[Path, str], ArchivePlan | ExtractPlan],
    ) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        plan = plan_with(tmp_path, "clobber")

        assert plan.can_run is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)


class TestExtractionMetadata:
    """Test that the metadata options reach the policy."""

    @pytest.mark.parametrize("opt_in", [False, True])
    def test_existing_directory_metadata_follows_the_option(
        self, tmp_path: Path, opt_in: bool
    ) -> None:
        """Test that only the opt-in re-dates a directory that already exists."""
        archive_path = tmp_path / "meta.tar"
        make_tar(
            archive_path, dir_entry("d", mtime=1_000_000_000), file_entry("d/a.txt")
        )
        out_dir = tmp_path / "out"
        (out_dir / "d").mkdir(parents=True)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(
                overwrite=OverwriteMode.OVERWRITE, overwrite_dir_metadata=opt_in
            ),
        ).run()

        assert result.ok, result.error
        restored = (out_dir / "d").stat().st_mtime == pytest.approx(
            1_000_000_000, abs=2
        )
        assert restored == opt_in

    def test_preserve_timestamps_off_uses_the_current_time(
        self, tmp_path: Path
    ) -> None:
        """Test that the stored mtime is ignored when the flag is off."""
        archive_path = tmp_path / "meta.tar"
        make_tar(archive_path, file_entry("a.txt", mode=0o644, mtime=1_000_000_000))
        out_dir = tmp_path / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_timestamps=False)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mtime > 1_000_000_000

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_preserve_permissions_off_leaves_the_created_mode(
        self, tmp_path: Path
    ) -> None:
        """Test that the stored mode is not applied when the flag is off."""
        archive_path = tmp_path / "meta.tar"
        make_tar(archive_path, file_entry("a.txt", mode=0o600, mtime=1_000_000_000))
        out_dir = tmp_path / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_permissions=False)
        ).run()

        assert result.ok, result.error
        created = (out_dir / "a.txt").stat().st_mode & 0o777
        assert created == 0o666 & ~umask()


BIG_PAYLOAD = b"compressible " * 4000


@dataclass
class ArchivedTree:
    """A small tree, and the entries planned for it in each archive format."""

    tree: Path
    entries: dict[str, dict[str, ArchiveEntry]]


class TestArchiveEntryMetadata:
    """Test what `plan_extraction` reports about each entry."""

    @pytest.fixture(scope="class")
    @classmethod
    def archived(cls, tmp_path_factory: pytest.TempPathFactory) -> ArchivedTree:
        """Archive a small tree once per format, and plan each extraction.

        Shared by the whole class, so tests must only read it.

        Args:
            tmp_path_factory: Pytest temporary path factory fixture.

        Returns:
            The tree, and each format's entries keyed by path without a
            trailing separator.
        """
        tmp = tmp_path_factory.mktemp("entry_metadata")
        tree = tmp / "tree"
        tree.mkdir()
        (tree / "big.bin").write_bytes(BIG_PAYLOAD)
        (tree / "small.txt").write_text("hello")
        (tree / "nested").mkdir()

        entries = {}
        for fmt in ("tar", "zip"):
            archive = tmp / f"out.{fmt}"
            options = ArchiveOptions(format=fmt)
            result = ArchiveJob.from_paths([tree], archive, options).run()
            assert result.ok, result.error

            plan = plan_extraction(archive, tmp / f"dest_{fmt}")
            assert plan.can_run, plan.reason_if_unavailable
            entries[fmt] = {e.path.rstrip("/"): e for e in plan.entries}

        return ArchivedTree(tree, entries)

    def test_entry_is_immutable(self) -> None:
        """Test that entries describe an archive that has already been written."""
        entry = ArchiveEntry(path="a.txt")

        with pytest.raises((AttributeError, TypeError)):
            entry.path = "b.txt"  # ty: ignore[invalid-assignment] - intended

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_mtime_is_reported(self, archived: ArchivedTree, fmt: str) -> None:
        """Test that mtime crosses the boundary rather than staying at its default."""
        entry = archived.entries[fmt]["tree/big.bin"]

        assert entry.mtime > 0
        # Written moments ago, so it cannot be far from now
        assert abs(entry.mtime - (archived.tree / "big.bin").stat().st_mtime) < 60

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_mode_is_reported(self, archived: ArchivedTree, fmt: str) -> None:
        """Test that mode is reported as permission bits rather than 0."""
        entries = archived.entries[fmt]

        assert entries["tree/big.bin"].mode & 0o400, "expected a readable file mode"
        assert entries["tree/nested"].mode & 0o100, "expected a searchable dir mode"

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_size_and_type_still_reported(
        self, archived: ArchivedTree, fmt: str
    ) -> None:
        """Test that the fields that already worked keep working."""
        entries = archived.entries[fmt]

        assert entries["tree/big.bin"].size == len(BIG_PAYLOAD)
        assert entries["tree/nested"].is_dir is True
        assert entries["tree/big.bin"].is_dir is False

    def test_zip_reports_per_entry_compression(self, archived: ArchivedTree) -> None:
        """Test that zip compresses each entry, so it can say how well."""
        entry = archived.entries["zip"]["tree/big.bin"]

        assert entry.compressed_size is not None
        assert 0 < entry.compressed_size < entry.size
        assert entry.crc is not None
        assert entry.method is not None

    def test_tar_omits_per_entry_compression(self, archived: ArchivedTree) -> None:
        """Test that tar compresses the whole stream, so there is nothing to report."""
        entry = archived.entries["tar"]["tree/big.bin"]

        assert entry.compressed_size is None
        assert entry.crc is None
        assert entry.method is None

    def test_zip_crc_matches_the_content(self, archived: ArchivedTree) -> None:
        """Test that the CRC reported is the one zip actually stored."""
        assert archived.entries["zip"]["tree/big.bin"].crc == zlib.crc32(BIG_PAYLOAD)

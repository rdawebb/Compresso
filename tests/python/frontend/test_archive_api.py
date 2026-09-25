"""Tests for the frontend archive API module."""

import io
import os
import struct
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
from pathlib import Path

import pytest

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


class TestArchiveOptions:
    """Test the ArchiveOptions dataclass."""

    def test_archive_options_default(self) -> None:
        """Test creating ArchiveOptions with defaults."""
        opts = ArchiveOptions()

        assert opts.format == "tar.zst"
        assert opts.compression_level is None
        assert opts.overwrite is OverwriteMode.RENAME

    def test_archive_options_with_format(self) -> None:
        """Test creating ArchiveOptions with a format and level."""
        opts = ArchiveOptions(format="tar.gz", compression_level=6)

        assert opts.format == "tar.gz"
        assert opts.compression_level == 6


class TestPlanArchive:
    """Test the plan_archive planner."""

    def test_plan_archive_valid(self, sample_text_file: Path, temp_dir: Path) -> None:
        """Test that a plan over existing sources can run and sums input size."""
        out = temp_dir / "out.tar.zst"
        plan = plan_archive([sample_text_file], out)

        assert isinstance(plan, ArchivePlan)
        assert plan.can_run is True
        assert plan.reason_if_unavailable is None
        assert plan.entry_count == 1
        assert plan.total_input_size == sample_text_file.stat().st_size

    def test_plan_archive_no_sources(self, temp_dir: Path) -> None:
        """Test that a plan with no sources cannot run."""
        plan = plan_archive([], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None

    def test_plan_archive_missing_source(self, temp_dir: Path) -> None:
        """Test that a plan referencing a missing source cannot run."""
        plan = plan_archive([temp_dir / "nope.txt"], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        if plan.reason_if_unavailable is not None:
            assert "does not exist" in plan.reason_if_unavailable

    def test_plan_archive_non_archive_format(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a single-file codec format cannot be used to build an archive."""
        opts = ArchiveOptions(format="gz")
        plan = plan_archive([sample_text_file], temp_dir / "out.gz", opts)

        assert plan.can_run is False
        if plan.reason_if_unavailable is not None:
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
        self, sample_text_file: Path, temp_dir: Path, fmt: str, level: int, match: str
    ) -> None:
        """Test that the stage that compresses decides which levels are valid."""
        opts = ArchiveOptions(format=fmt, compression_level=level)
        plan = plan_archive([sample_text_file], temp_dir / f"out.{fmt}", opts)

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None
        assert match in plan.reason_if_unavailable

    def test_plan_archive_codec_stage_level(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that tar.zst takes zstd's levels even though tar has none."""
        opts = ArchiveOptions(format="tar.zst", compression_level=19)
        assert plan_archive([sample_text_file], temp_dir / "o.tar.zst", opts).can_run


class TestArchiveJob:
    """Test the ArchiveJob class."""

    def test_from_paths_builds_plan(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that from_paths runs the planner and exposes the plan."""
        job = ArchiveJob.from_paths([sample_text_file], temp_dir / "out.tar.zst")

        assert isinstance(job.plan, ArchivePlan)
        assert job.plan.can_run is True

    def test_run_on_unavailable_plan_returns_failed_result(
        self, temp_dir: Path
    ) -> None:
        """Test that run() never raises: an unrunnable plan yields a failed JobResult."""
        job = ArchiveJob.from_paths([], temp_dir / "out.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False
        assert result.error is not None


class TestArchiveOverwrite:
    """Test the four `overwrite` modes against an existing destination archive."""

    #: The numbered-sibling suffix `RENAME` inserts before the extension:
    #: "name 2.ext" on macOS, "name (2).ext" elsewhere.
    CONFLICT_SUFFIX = " {}" if sys.platform == "darwin" else " ({})"

    @staticmethod
    def _source_and_stale_output(temp_dir: Path) -> tuple[Path, Path]:
        """Build a source file and a stale file already at the archive's path.

        Args:
            temp_dir: Directory to build both in.

        Returns:
            The source path, and the archive path already occupied by an
            unrelated file.
        """
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = temp_dir / "out.tar"
        archive_path.write_bytes(b"stale")

        return source, archive_path

    def test_renames_existing_archive_by_default(self, temp_dir: Path) -> None:
        """Test that the default policy renames rather than overwriting."""
        source, archive_path = self._source_and_stale_output(temp_dir)
        job = ArchiveJob.from_paths([source], archive_path)

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() == b"stale"
        renamed = temp_dir / f"out{self.CONFLICT_SUFFIX.format(2)}.tar"
        assert job.plan.output == renamed
        assert renamed.is_file()
        assert renamed.read_bytes() != b"stale"

    def test_rename_keeps_a_compound_extension_intact(self, temp_dir: Path) -> None:
        """Test that "out.tar.zst" renames to "out 2.tar.zst", not "out.tar 2.zst".

        `create_archive` writes a codec format's output only at the very end,
        through a separate temp-file stage - a case the plain single-extension
        rename doesn't exercise.
        """
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = temp_dir / "out.tar.zst"
        archive_path.write_bytes(b"stale")

        job = ArchiveJob.from_paths(
            [source], archive_path, options=ArchiveOptions(format="tar.zst")
        )
        result = job.run()

        assert result.ok, result.error
        renamed = temp_dir / f"out{self.CONFLICT_SUFFIX.format(2)}.tar.zst"
        assert job.plan.output == renamed
        assert renamed.is_file()

    def test_rename_increments_through_repeated_conflicts(self, temp_dir: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        source, archive_path = self._source_and_stale_output(temp_dir)
        options = ArchiveOptions(format="tar", overwrite=OverwriteMode.RENAME)

        assert ArchiveJob.from_paths([source], archive_path, options).run().ok
        result = ArchiveJob.from_paths([source], archive_path, options).run()

        assert result.ok, result.error
        assert (temp_dir / f"out{self.CONFLICT_SUFFIX.format(2)}.tar").exists()
        assert (temp_dir / f"out{self.CONFLICT_SUFFIX.format(3)}.tar").exists()

    def test_error_mode_refuses_existing_archive(self, temp_dir: Path) -> None:
        """Test that explicit `error` refuses to touch an existing archive."""
        source, archive_path = self._source_and_stale_output(temp_dir)

        result = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.ERROR),
        ).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert archive_path.read_bytes() == b"stale"

    def test_skip_leaves_the_existing_archive(self, temp_dir: Path) -> None:
        """Test that `skip` leaves the stale archive untouched and still succeeds."""
        source, archive_path = self._source_and_stale_output(temp_dir)
        job = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.SKIP),
        )

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() == b"stale"
        assert job.plan.output == archive_path

    def test_overwrite_replaces_the_existing_archive(self, temp_dir: Path) -> None:
        """Test that `overwrite` replaces the stale archive in place."""
        source, archive_path = self._source_and_stale_output(temp_dir)
        job = ArchiveJob.from_paths(
            [source],
            archive_path,
            options=ArchiveOptions(format="tar", overwrite=OverwriteMode.OVERWRITE),
        )

        result = job.run()

        assert result.ok, result.error
        assert archive_path.read_bytes() != b"stale"
        assert job.plan.output == archive_path

    def test_plain_string_mode_is_normalised(self, temp_dir: Path) -> None:
        """Test that a mode given as a plain string still plans as the enum."""
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")

        plan = ArchiveJob.from_paths(
            [source],
            temp_dir / "out.tar",
            options=ArchiveOptions(format="tar", overwrite="skip"),  # ty: ignore
        ).plan

        assert plan.can_run is True
        assert plan.options.overwrite is OverwriteMode.SKIP

    def test_unknown_mode_is_an_unavailable_plan(self, temp_dir: Path) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")

        plan = ArchiveJob.from_paths(
            [source],
            temp_dir / "out.tar",
            options=ArchiveOptions(format="tar", overwrite="clobber"),  # ty: ignore
        ).plan

        assert plan.can_run is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)


class TestExtractJob:
    """Test the ExtractJob class."""

    def test_from_archive_missing_returns_unavailable_plan(
        self, temp_dir: Path
    ) -> None:
        """Test that a missing archive produces a plan that cannot run."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")

        assert isinstance(job.plan, ExtractPlan)
        assert job.plan.can_run is False

    def test_run_on_unavailable_plan_returns_failed_result(
        self, temp_dir: Path
    ) -> None:
        """Test that run() never raises on a missing archive."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False

    def test_output_dir_defaults_to_the_archives_own_directory(
        self, temp_dir: Path, monkeypatch
    ) -> None:
        """Test that omitting output_dir plans beside the archive, not in the cwd."""
        nested: Path = temp_dir / "nested"
        nested.mkdir()
        monkeypatch.chdir(temp_dir)

        plan = ExtractJob.from_archive(nested / "missing.tar.zst").plan

        assert plan.output_dir == nested.resolve()

    def test_output_dir_default_ignores_a_relative_archive_path(
        self, temp_dir: Path, monkeypatch
    ) -> None:
        """Test that a relative archive path still resolves to a real directory."""
        monkeypatch.chdir(temp_dir)

        plan = ExtractJob.from_archive("missing.tar.zst").plan

        assert plan.output_dir == temp_dir.resolve()


class TestArchiveRoundTrip:
    """Test end-to-end archive -> extract round-trip."""

    def test_round_trip(self, temp_dir: Path, monkeypatch) -> None:
        """Test that archiving then extracting restores the original file contents.

        Archives store source paths verbatim and extraction refuses absolute
        paths, so archive from within the working directory using a relative
        source name (matching real CLI usage).
        """
        monkeypatch.chdir(temp_dir)
        src = Path("data.txt")
        src.write_bytes(b"hello compresso" * 100)
        archive_path = Path("bundle.tar.zst")

        archive_result = ArchiveJob.from_paths([src], archive_path).run()
        assert archive_result.ok, archive_result.error
        assert archive_path.is_file()

        # list_contents surfaces real ArchiveEntry objects.
        extract_job = ExtractJob.from_archive(archive_path, temp_dir / "restore")
        entries = extract_job.list_contents()
        assert entries
        assert all(isinstance(e, ArchiveEntry) for e in entries)

        extract_result = extract_job.run()
        assert extract_result.ok, extract_result.error

        restored = temp_dir / "restore" / src.name
        assert restored.read_bytes() == src.read_bytes()

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_plan_entries_carry_sizes(
        self, temp_dir: Path, monkeypatch, fmt: str
    ) -> None:
        """Test that planned entries report each file's uncompressed size.

        The CLI sums these to decide whether to show a progress bar.
        """
        monkeypatch.chdir(temp_dir)
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

    def test_directory_round_trip(self, temp_dir: Path, monkeypatch) -> None:
        """Test that archiving a directory preserves its nested structure on extraction."""
        monkeypatch.chdir(temp_dir)
        src = Path("tree")
        (src / "deep").mkdir(parents=True)
        (src / "top.txt").write_bytes(b"top level")
        (src / "deep" / "leaf.txt").write_bytes(b"leaf content")
        archive_path = Path("tree.tar.zst")

        assert ArchiveJob.from_paths([src], archive_path).run().ok

        names = {e.path for e in ExtractJob.from_archive(archive_path).list_contents()}
        assert "tree/top.txt" in names
        assert "tree/deep/leaf.txt" in names

        extract_result = ExtractJob.from_archive(archive_path, temp_dir / "out").run()
        assert extract_result.ok, extract_result.error

        assert (temp_dir / "out" / "tree" / "top.txt").read_bytes() == b"top level"
        assert (
            temp_dir / "out" / "tree" / "deep" / "leaf.txt"
        ).read_bytes() == b"leaf content"

    def test_non_ascii_names_round_trip(self, temp_dir: Path, monkeypatch) -> None:
        """Test that non-ASCII names survive as both a source path and an entry name.

        Names are compared after NFC normalisation: macOS stores them decomposed,
        so a name read back off the filesystem is not byte-identical to the
        composed literal written here.
        """
        monkeypatch.chdir(temp_dir)
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

        out_dir = temp_dir / "sortie"
        extract_result = ExtractJob.from_archive(archive_path, out_dir).run()
        assert extract_result.ok, extract_result.error

        assert (out_dir / "données" / "café.txt").read_bytes() == b"contenu"
        assert (out_dir / "données" / "日本語.txt").read_bytes() == b"content"

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_absolute_source_directory_round_trip(
        self, temp_dir: Path, fmt: str
    ) -> None:
        """Test that an absolute source directory is stored relative to its parent."""
        src = temp_dir / "tree"
        (src / "deep").mkdir(parents=True)
        (src / "top.txt").write_bytes(b"top level")
        (src / "deep" / "leaf.txt").write_bytes(b"leaf content")
        archive_path = temp_dir / f"tree.{fmt}"

        options = ArchiveOptions(format=fmt)
        result = ArchiveJob.from_paths([src.resolve()], archive_path, options).run()
        assert result.ok, result.error

        names = {e.path for e in ExtractJob.from_archive(archive_path).list_contents()}
        assert "tree/top.txt" in names
        assert "tree/deep/leaf.txt" in names
        for name in names:
            assert not name.startswith(("/", "\\")), name
            assert "\\" not in name, name

        out_dir = temp_dir / "out"
        extract_result = ExtractJob.from_archive(archive_path, out_dir).run()
        assert extract_result.ok, extract_result.error

        assert (out_dir / "tree" / "top.txt").read_bytes() == b"top level"
        assert (out_dir / "tree" / "deep" / "leaf.txt").read_bytes() == b"leaf content"


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlink creation needs privilege on Windows"
)
class TestArchivingSymlinks:
    """Test that a symlink is archived as a symlink, not as what it points at.

    Written against an uncompressed tar, checked both through `tarfile` and
    through the entries an extraction plan lists.
    """

    def test_symlink_is_stored_as_a_link(self, temp_dir: Path, monkeypatch) -> None:
        """Test that a symlink becomes a link entry rather than a copy of its target."""
        monkeypatch.chdir(temp_dir)
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

    def test_symlink_loop_does_not_recurse(self, temp_dir: Path, monkeypatch) -> None:
        """Test that a symlink pointing at its own parent terminates the walk."""
        monkeypatch.chdir(temp_dir)
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


def _tar_with_entry(archive_path: Path, entry_name: str) -> None:
    """Write a tar holding one regular file stored verbatim under `entry_name`.

    Goes through `tarfile` rather than `ArchiveJob` because the point is to
    store names the archiver itself would never produce.

    Args:
        archive_path: Where to write the tar.
        entry_name: The entry name to store, used exactly as given.
    """
    payload = b"pwned"
    with tarfile.open(archive_path, "w", format=tarfile.GNU_FORMAT) as tf:
        info = tarfile.TarInfo(entry_name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))


class TestExtractionRefusesUnsafePaths:
    """Test that extraction refuses entry names that escape the output directory.

    The Windows-absolute forms are rejected on POSIX too, so every case here
    runs on all platforms rather than only on the Windows CI leg.
    """

    @pytest.mark.parametrize(
        "entry_name",
        [
            "/etc/passwd",  # POSIX absolute
            "\\evil.txt",  # Windows root-relative
            "C:\\Windows\\evil.txt",  # Windows drive-absolute
            "C:/Windows/evil.txt",  # ... with the other separator
            "C:evil.txt",  # Windows drive-relative
            "\\\\server\\share\\evil.txt",  # UNC
            "\\\\?\\C:\\evil.txt",  # Extended-length prefix
        ],
    )
    def test_rejects_absolute_entry(self, temp_dir: Path, entry_name: str) -> None:
        """Test that an absolute or drive-qualified entry name fails extraction."""
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, entry_name)

        result = ExtractJob.from_archive(archive_path, temp_dir / "out").run()

        assert result.ok is False
        assert "absolute path" in str(result.error)

    @pytest.mark.parametrize("entry_name", ["../escape.txt", "sub/../../escape.txt"])
    def test_rejects_parent_traversal(self, temp_dir: Path, entry_name: str) -> None:
        """Test that an entry climbing out of the output directory is refused."""
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, entry_name)

        result = ExtractJob.from_archive(archive_path, temp_dir / "out").run()

        assert result.ok is False
        assert "traversal" in str(result.error)
        assert not (temp_dir / "escape.txt").exists()

    def test_backslash_traversal_stays_contained(self, temp_dir: Path) -> None:
        """Test that a backslash-separated traversal cannot escape on any platform.

        On Windows `..\\..\\escape.txt` is a real traversal and is refused; on
        POSIX it is one oddly-named file; assert containment first, then each
        platform's verdict.
        """
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, "..\\..\\escape.txt")
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert not (temp_dir / "escape.txt").exists()
        assert not (temp_dir.parent / "escape.txt").exists()

        if sys.platform == "win32":
            assert result.ok is False
            assert "traversal" in str(result.error)

        else:
            assert result.ok, result.error
            assert (out_dir / "..\\..\\escape.txt").read_bytes() == b"pwned"

    @pytest.mark.parametrize(
        "entry_name", ["notes.txt:evil.exe", "notes.txt:evil.exe:$DATA"]
    )
    def test_alternate_data_stream_writes_no_stream(
        self, temp_dir: Path, entry_name: str
    ) -> None:
        """Test that an NTFS stream entry never attaches content to a real file.

        On Windows the colon is a stream separator and the entry is refused; on
        POSIX it is an ordinary filename character, so the entry extracts as one
        literally-named file; assert the property that holds on both, then the
        rejection only where it applies.
        """
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, entry_name)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert not (out_dir / "notes.txt").exists()

        if sys.platform == "win32":
            assert result.ok is False
            assert "alternate data stream" in str(result.error)

        else:
            assert result.ok, result.error
            assert (out_dir / entry_name).read_bytes() == b"pwned"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlink creation needs privilege on Windows"
    )
    def test_rejects_entry_writing_through_a_symlinked_component(
        self, temp_dir: Path
    ) -> None:
        """Test that an entry cannot write through a symlink already in the output."""
        outside = temp_dir / "outside"
        outside.mkdir()
        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "link").symlink_to(outside, target_is_directory=True)

        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, "link/sub/escape.txt")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "traversal" in str(result.error)
        assert not (outside / "sub" / "escape.txt").exists()
        assert not (outside / "sub").exists()

    @pytest.mark.skipif(
        sys.platform != "win32", reason="junctions are a Windows filesystem feature"
    )
    def test_rejects_entry_writing_through_a_junction(self, temp_dir: Path) -> None:
        """Test the junction form of the symlinked-component escape.

        The Windows equivalent of the test above; junctions are used rather than
        `mklink /D` symlinks because creating one needs no admin rights.
        """
        outside = temp_dir / "outside"
        outside.mkdir()
        out_dir = temp_dir / "out"
        out_dir.mkdir()
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(out_dir / "link"), str(outside)],
            check=True,
            capture_output=True,
        )

        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, "link/sub/escape.txt")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "traversal" in str(result.error)
        assert not (outside / "sub" / "escape.txt").exists()
        assert not (outside / "sub").exists()

    def test_accepts_nested_entry(self, temp_dir: Path) -> None:
        """Test that a legitimate nested entry is still extracted.

        Guards the containment check against over-rejecting: it compares the
        byte after the resolved root prefix, which is a backslash on Windows.
        """
        archive_path = temp_dir / "nested.tar"
        _tar_with_entry(archive_path, "sub/deep/leaf.txt")
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "sub" / "deep" / "leaf.txt").read_bytes() == b"pwned"


def _tar_with_entries(archive_path: Path, entries: list[tarfile.TarInfo]) -> None:
    """Write a tar holding `entries`, each with `size` bytes of filler.

    Args:
        archive_path: Where to write the tar.
        entries: Entry headers to store, used exactly as given.
    """
    with tarfile.open(archive_path, "w", format=tarfile.GNU_FORMAT) as tf:
        for info in entries:
            data = io.BytesIO(b"x" * info.size) if info.size else None
            tf.addfile(info, data)


def _file_entry(name: str, size: int = 5) -> tarfile.TarInfo:
    """Build a regular-file tar header of `size` bytes.

    Args:
        name: Entry name to store.
        size: Size of the entry's filler payload.

    Returns:
        The tar header.
    """
    info = tarfile.TarInfo(name)
    info.size = size
    return info


def _dir_entry(name: str) -> tarfile.TarInfo:
    """Build a directory tar header.

    Args:
        name: Entry name to store.

    Returns:
        The tar header.
    """
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


class TestExtractionSizeCap:
    """Test that `max_total_size` bounds what an archive can write."""

    def test_no_cap_by_default(self, temp_dir: Path) -> None:
        """Test that the shipped default places no limit on extracted bytes."""
        archive_path = temp_dir / "big.tar"
        _tar_with_entries(archive_path, [_file_entry("big.bin", 200_000)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "big.bin").stat().st_size == 200_000

    def test_refuses_archive_over_the_cap(self, temp_dir: Path) -> None:
        """Test that a total past the cap is refused with nothing written."""
        archive_path = temp_dir / "bomb.tar"
        _tar_with_entries(archive_path, [_file_entry("big.bin", 200_000)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_total_size=1000)
        ).run()

        assert result.ok is False
        assert "maximum extracted size" in str(result.error)
        assert list(out_dir.iterdir()) == []

    def test_cap_applies_to_the_total_not_each_entry(self, temp_dir: Path) -> None:
        """Test that entries individually under the cap still fail in total."""
        archive_path = temp_dir / "many.tar"
        _tar_with_entries(
            archive_path, [_file_entry(f"f{i}.bin", 400) for i in range(10)]
        )
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_total_size=1000)
        ).run()

        assert result.ok is False
        assert "maximum extracted size" in str(result.error)

    def test_entry_under_the_cap_extracts(self, temp_dir: Path) -> None:
        """Test that the cap does not over-reject an archive that fits."""
        archive_path = temp_dir / "small.tar"
        _tar_with_entries(archive_path, [_file_entry("small.bin", 500)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_total_size=1000)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "small.bin").stat().st_size == 500

    def test_understated_entry_size_is_still_capped(self, temp_dir: Path) -> None:
        """Test that the cap counts bytes written, not the declared size.

        A zip's central directory is the only thing the pre-extraction pass can
        read sizes from, and it is attacker-controlled: patched to claim ten
        bytes, this entry passes that pass and has to be stopped mid-write.
        """
        archive_path = temp_dir / "lie.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", b"x" * 200_000)

        data = bytearray(archive_path.read_bytes())
        central_dir = data.find(b"PK\x01\x02")
        struct.pack_into("<I", data, central_dir + 24, 10)  # Uncompressed size
        archive_path.write_bytes(bytes(data))

        out_dir = temp_dir / "out"
        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_total_size=1000)
        ).run()

        assert result.ok is False
        assert "maximum extracted size" in str(result.error)
        assert (out_dir / "big.bin").stat().st_size == 0


class TestExtractionDepthLimit:
    """Test that `max_depth` bounds how deeply an entry may nest."""

    def test_refuses_entry_past_the_depth_limit(self, temp_dir: Path) -> None:
        """Test that an entry nested past the default limit is refused."""
        archive_path = temp_dir / "deep.tar"
        name = "/".join(f"d{i}" for i in range(40)) + "/leaf.txt"
        _tar_with_entries(archive_path, [_file_entry(name)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "max depth" in str(result.error)
        assert list(out_dir.iterdir()) == []

    def test_accepts_entry_within_the_depth_limit(self, temp_dir: Path) -> None:
        """Test that an entry just inside the limit still extracts."""
        archive_path = temp_dir / "deep.tar"
        parts = [f"d{i}" for i in range(31)]
        _tar_with_entries(archive_path, [_file_entry("/".join([*parts, "leaf.txt"]))])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert out_dir.joinpath(*parts, "leaf.txt").exists()

    def test_depth_limit_is_configurable(self, temp_dir: Path) -> None:
        """Test that a tighter limit refuses what the default accepts."""
        archive_path = temp_dir / "nested.tar"
        _tar_with_entries(archive_path, [_file_entry("a/b/c/leaf.txt")])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_depth=2)
        ).run()

        assert result.ok is False
        assert "max depth" in str(result.error)


class TestExtractionOverwrite:
    """Test the four `overwrite` modes against an existing destination."""

    #: The numbered-sibling suffix `RENAME` inserts before the extension:
    #: "name 2.ext" on macOS, "name (2).ext" elsewhere.
    CONFLICT_SUFFIX = " {}" if sys.platform == "darwin" else " ({})"

    @staticmethod
    def _archive_and_stale_output(temp_dir: Path) -> tuple[Path, Path]:
        """Build a one-entry archive and an output dir already holding that name.

        Args:
            temp_dir: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = temp_dir / "one.tar"
        _tar_with_entries(archive_path, [_file_entry("a.txt")])

        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "a.txt").write_bytes(b"original")

        return archive_path, out_dir

    def test_renames_existing_file_by_default(self, temp_dir: Path) -> None:
        """Test that the default policy renames rather than refusing."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"original"
        renamed = out_dir / f"a{self.CONFLICT_SUFFIX.format(2)}.txt"
        assert renamed.read_bytes() == b"xxxxx"

    def test_rename_increments_through_repeated_conflicts(self, temp_dir: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)
        options = ExtractOptions(overwrite=OverwriteMode.RENAME)

        assert ExtractJob.from_archive(archive_path, out_dir, options=options).run().ok
        result = ExtractJob.from_archive(archive_path, out_dir, options=options).run()

        assert result.ok, result.error
        assert (out_dir / f"a{self.CONFLICT_SUFFIX.format(2)}.txt").exists()
        assert (out_dir / f"a{self.CONFLICT_SUFFIX.format(3)}.txt").exists()

    def test_error_mode_still_refuses_on_request(self, temp_dir: Path) -> None:
        """Test that explicit `error` reproduces the old refuse-by-default behavior."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.ERROR)
        ).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_skip_leaves_the_existing_file(self, temp_dir: Path) -> None:
        """Test that `skip` succeeds without replacing what is already there."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.SKIP)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_overwrite_replaces_the_existing_file(self, temp_dir: Path) -> None:
        """Test that `overwrite` replaces the file's contents."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.OVERWRITE),
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"xxxxx"

    @staticmethod
    def _archive_with_dir_and_blocking_file(temp_dir: Path) -> tuple[Path, Path]:
        """Build a one-directory-entry archive and a same-named file blocking it.

        Args:
            temp_dir: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = temp_dir / "dir.tar"
        _tar_with_entries(archive_path, [_dir_entry("d")])

        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        return archive_path, out_dir

    def test_dir_clash_with_a_file_renames_by_default(self, temp_dir: Path) -> None:
        """Test that the default policy renames the directory rather than refusing."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(temp_dir)

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "d").read_bytes() == b"blocking file"
        renamed = out_dir / f"d{self.CONFLICT_SUFFIX.format(2)}"
        assert renamed.is_dir()

    def test_dir_clash_with_a_file_renames_nested_entries_too(
        self, temp_dir: Path
    ) -> None:
        """Test that entries nested under the renamed directory follow it."""
        archive_path = temp_dir / "dir.tar"
        _tar_with_entries(archive_path, [_dir_entry("d"), _file_entry("d/inside.txt")])

        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        renamed = out_dir / f"d{self.CONFLICT_SUFFIX.format(2)}"
        assert renamed.is_dir()
        assert (renamed / "inside.txt").read_bytes() == b"xxxxx"
        # The original "d" is untouched, and nothing was created back under it
        assert (out_dir / "d").read_bytes() == b"blocking file"

    def test_doubly_nested_dir_clash_composes_with_parent_rename(
        self, temp_dir: Path
    ) -> None:
        """Test that a second clash below an already-renamed ancestor composes."""
        archive_path = temp_dir / "dir.tar"
        suffix2 = self.CONFLICT_SUFFIX.format(2)
        _tar_with_entries(
            archive_path,
            [
                _dir_entry("a"),
                _file_entry(f"a{suffix2}/b"),
                _dir_entry("a/b"),
                _file_entry("a/b/inside.txt"),
            ],
        )

        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "a").write_bytes(b"blocks a")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        renamed_a = out_dir / f"a{suffix2}"
        assert (renamed_a / "b").read_bytes() == b"xxxxx"  # the unrelated entry
        renamed_b = renamed_a / f"b{suffix2}"
        assert renamed_b.is_dir()
        assert (renamed_b / "inside.txt").read_bytes() == b"xxxxx"
        # Nothing was planted back under the archive's own, un-renamed "a/b"
        assert not (out_dir / "a" / "b").exists()

    def test_dir_clash_with_an_existing_dir_renames_by_default(
        self, temp_dir: Path
    ) -> None:
        """Test that re-extracting over an existing directory renames the
        directory itself rather than merging into it."""
        archive_path = temp_dir / "dir.tar"
        _tar_with_entries(archive_path, [_dir_entry("d"), _file_entry("d/inside.txt")])

        out_dir = temp_dir / "out"
        out_dir.mkdir()
        (out_dir / "d").mkdir()
        (out_dir / "d" / "inside.txt").write_bytes(b"original")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        # The original directory and its contents are untouched
        assert (out_dir / "d" / "inside.txt").read_bytes() == b"original"

        renamed = out_dir / f"d{self.CONFLICT_SUFFIX.format(2)}"
        assert renamed.is_dir()
        # The nested file lands under the renamed directory with its own
        # name unchanged - not doubled up or given its own " 2" suffix
        assert (renamed / "inside.txt").read_bytes() == b"xxxxx"

    def test_dir_clash_with_an_existing_dir_renames_subdirs_too(
        self, temp_dir: Path
    ) -> None:
        """Test that subdirectories nested under a clashing directory follow
        the rename instead of being merged into the pre-existing tree, which
        would otherwise leave the files beneath them doubled up."""
        archive_path = temp_dir / "dir.tar"
        _tar_with_entries(
            archive_path,
            [
                _dir_entry("d"),
                _dir_entry("d/sub"),
                _file_entry("d/sub/inside.txt"),
            ],
        )

        out_dir = temp_dir / "out"
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

        renamed = out_dir / f"d{self.CONFLICT_SUFFIX.format(2)}"
        assert renamed.is_dir()
        assert sorted(p.name for p in renamed.iterdir()) == ["sub"]
        assert (renamed / "sub" / "inside.txt").read_bytes() == b"xxxxx"

    def test_dir_clash_with_a_file_errors_in_error_mode(self, temp_dir: Path) -> None:
        """Test that explicit `error` also refuses, not the old silent no-op."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(temp_dir)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.ERROR),
        ).run()

        assert result.ok is False
        assert "already exists" in str(result.error)

    def test_dir_clash_with_a_file_is_skipped_in_skip_mode(
        self, temp_dir: Path
    ) -> None:
        """Test that `skip` leaves the blocking file untouched."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(temp_dir)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite=OverwriteMode.SKIP)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "d").is_file()
        assert (out_dir / "d").read_bytes() == b"blocking file"

    def test_dir_clash_with_a_file_is_replaced_in_overwrite_mode(
        self, temp_dir: Path
    ) -> None:
        """Test that `overwrite` replaces the blocking file with the directory."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(temp_dir)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.OVERWRITE),
        ).run()

        assert result.ok, result.error
        assert (out_dir / "d").is_dir()

    def test_plain_string_mode_is_normalised(self, temp_dir: Path) -> None:
        """Test that a mode given as a plain string still plans as the enum."""
        archive_path = temp_dir / "one.tar"
        _tar_with_entries(archive_path, [_file_entry("a.txt")])

        plan = ExtractJob.from_archive(
            archive_path,
            temp_dir / "out",
            options=ExtractOptions(overwrite="skip"),  # ty: ignore
        ).plan

        assert plan.can_run is True
        assert plan.options.overwrite is OverwriteMode.SKIP

    def test_unknown_mode_is_an_unavailable_plan(self, temp_dir: Path) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        archive_path = temp_dir / "one.tar"
        _tar_with_entries(archive_path, [_file_entry("a.txt")])

        plan = ExtractJob.from_archive(
            archive_path,
            temp_dir / "out",
            options=ExtractOptions(overwrite="clobber"),  # ty: ignore
        ).plan

        assert plan.can_run is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)


class TestExtractionMetadata:
    """Test that modes and modification times are restored as the policy says."""

    @staticmethod
    def _archive_with_metadata(archive_path: Path, mtime: int, mode: int) -> None:
        """Write a one-entry tar carrying an explicit mtime and mode.

        Args:
            archive_path: Where to write the tar.
            mtime: Modification time to store, in seconds since the epoch.
            mode: Permission bits to store.
        """
        info = _file_entry("a.txt")
        info.mtime = mtime
        info.mode = mode
        _tar_with_entries(archive_path, [info])

    def test_restores_mtime_by_default(self, temp_dir: Path) -> None:
        """Test that the entry's modification time survives extraction."""
        archive_path = temp_dir / "meta.tar"
        self._archive_with_metadata(archive_path, mtime=1_000_000_000, mode=0o644)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mtime == pytest.approx(
            1_000_000_000, abs=2
        )

    def test_preserve_timestamps_off_uses_the_current_time(
        self, temp_dir: Path
    ) -> None:
        """Test that the stored mtime is ignored when the flag is off."""
        archive_path = temp_dir / "meta.tar"
        self._archive_with_metadata(archive_path, mtime=1_000_000_000, mode=0o644)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_timestamps=False)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mtime > 1_000_000_000

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_restores_mode_by_default(self, temp_dir: Path) -> None:
        """Test that the entry's permission bits survive extraction."""
        archive_path = temp_dir / "meta.tar"
        self._archive_with_metadata(archive_path, mtime=1_000_000_000, mode=0o640)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mode & 0o777 == 0o640

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_preserve_permissions_off_leaves_the_created_mode(
        self, temp_dir: Path
    ) -> None:
        """Test that the stored mode is not applied when the flag is off."""
        archive_path = temp_dir / "meta.tar"
        self._archive_with_metadata(archive_path, mtime=1_000_000_000, mode=0o600)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_permissions=False)
        ).run()

        assert result.ok, result.error
        created = (out_dir / "a.txt").stat().st_mode & 0o777
        assert created == 0o666 & ~_umask()


def _umask() -> int:
    """Read the process umask without leaving it changed.

    Returns:
        The current umask.
    """
    current = os.umask(0)
    os.umask(current)
    return current


class TestExtractionRefusesSpecialFiles:
    """Test that device nodes, FIFOs and sockets are refused."""

    def test_refuses_fifo_entry(self, temp_dir: Path) -> None:
        """Test that a FIFO entry is classified and refused, not written."""
        archive_path = temp_dir / "fifo.tar"
        info = tarfile.TarInfo("pipe")
        info.type = tarfile.FIFOTYPE
        _tar_with_entries(archive_path, [info])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "special file" in str(result.error)
        assert not (out_dir / "pipe").exists()


class TestExtractionIsAllOrNothing:
    """Test that a refused entry stops anything from being written."""

    def test_later_bad_entry_blocks_the_earlier_good_one(self, temp_dir: Path) -> None:
        """Test that an escape in the second entry keeps the first off disk."""
        archive_path = temp_dir / "mixed.tar"
        _tar_with_entries(
            archive_path, [_file_entry("good.txt"), _file_entry("../escape.txt")]
        )
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "traversal" in str(result.error)
        assert not (out_dir / "good.txt").exists()
        assert not (temp_dir / "escape.txt").exists()

    def test_parent_segment_check_allows_a_leading_dot_name(
        self, temp_dir: Path
    ) -> None:
        """Test that `..data` is a filename, not a traversal."""
        archive_path = temp_dir / "dots.tar"
        _tar_with_entries(archive_path, [_file_entry("..data")])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "..data").read_bytes() == b"xxxxx"


class TestArchiveEntryMetadata:
    """Test what `plan_extraction` reports about each entry."""

    @staticmethod
    def _entries(tmp: Path, fmt: str, ext: str) -> dict[str, ArchiveEntry]:
        """Test that a small tree is archived and return its entries keyed by path."""
        tree = tmp / "tree"
        tree.mkdir()
        (tree / "big.bin").write_bytes(b"compressible " * 4000)
        (tree / "small.txt").write_text("hello")
        (tree / "nested").mkdir()

        archive = tmp / f"out{ext}"
        assert (
            ArchiveJob.from_paths(
                sources=[tree], output=archive, options=ArchiveOptions(format=fmt)
            )
            .run()
            .ok
        )

        plan = plan_extraction(archive, tmp / "dest")
        assert plan.can_run, plan.reason_if_unavailable
        return {e.path.rstrip("/"): e for e in plan.entries}

    def test_entry_is_immutable(self, temp_dir: Path) -> None:
        """Test that entries describe an archive that has already been written."""
        entry = ArchiveEntry(path="a.txt")

        with pytest.raises((AttributeError, TypeError)):
            entry.path = "b.txt"  # ty: ignore[invalid-assignment] - intended

    @pytest.mark.parametrize(("fmt", "ext"), [("tar", ".tar"), ("zip", ".zip")])
    def test_mtime_is_reported(self, temp_dir: Path, fmt: str, ext: str) -> None:
        """Test that mtime crosses the boundary rather than staying at its default."""
        entries = self._entries(temp_dir, fmt, ext)

        entry = entries["tree/big.bin"]
        assert entry.mtime > 0
        # Written moments ago, so it cannot be far from now
        assert abs(entry.mtime - (temp_dir / "tree" / "big.bin").stat().st_mtime) < 60

    @pytest.mark.parametrize(("fmt", "ext"), [("tar", ".tar"), ("zip", ".zip")])
    def test_mode_is_reported(self, temp_dir: Path, fmt: str, ext: str) -> None:
        """Test that mode is reported as permission bits rather than 0."""
        entries = self._entries(temp_dir, fmt, ext)

        assert entries["tree/big.bin"].mode & 0o400, "expected a readable file mode"
        assert entries["tree/nested"].mode & 0o100, "expected a searchable dir mode"

    @pytest.mark.parametrize(("fmt", "ext"), [("tar", ".tar"), ("zip", ".zip")])
    def test_size_and_type_still_reported(
        self, temp_dir: Path, fmt: str, ext: str
    ) -> None:
        """Test that the fields that already worked keep working."""
        entries = self._entries(temp_dir, fmt, ext)

        assert entries["tree/big.bin"].size == len(b"compressible " * 4000)
        assert entries["tree/nested"].is_dir is True
        assert entries["tree/big.bin"].is_dir is False

    def test_zip_reports_per_entry_compression(self, temp_dir: Path) -> None:
        """Test that zip compresses each entry, so it can say how well."""
        entries = self._entries(temp_dir, "zip", ".zip")
        entry = entries["tree/big.bin"]

        assert entry.compressed_size is not None
        assert 0 < entry.compressed_size < entry.size
        assert entry.crc is not None
        assert entry.method is not None

    def test_tar_omits_per_entry_compression(self, temp_dir: Path) -> None:
        """Test that tar compresses the whole stream, so there is nothing to report."""
        entries = self._entries(temp_dir, "tar", ".tar")
        entry = entries["tree/big.bin"]

        assert entry.compressed_size is None
        assert entry.crc is None
        assert entry.method is None

    def test_zip_crc_matches_the_content(self, temp_dir: Path) -> None:
        """Test that the CRC reported is the one zip actually stored."""
        import zlib

        entries = self._entries(temp_dir, "zip", ".zip")
        payload = b"compressible " * 4000

        assert entries["tree/big.bin"].crc == zlib.crc32(payload)

    def test_symlink_target_is_reported(self, temp_dir: Path) -> None:
        """Test that tar records a symlink as one, with its target."""
        tree = temp_dir / "tree"
        tree.mkdir()
        (tree / "real.txt").write_text("hello")
        os.symlink("real.txt", tree / "link")

        archive = temp_dir / "out.tar"
        assert (
            ArchiveJob.from_paths(
                sources=[tree], output=archive, options=ArchiveOptions(format="tar")
            )
            .run()
            .ok
        )

        entries = {
            e.path.rstrip("/"): e
            for e in plan_extraction(archive, temp_dir / "dest").entries
        }

        assert entries["tree/link"].is_symlink is True
        assert entries["tree/link"].link_target == "real.txt"
        assert entries["tree/real.txt"].link_target is None

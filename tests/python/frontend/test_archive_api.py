"""Tests for the frontend archive API module."""

import io
import sys
import tarfile
from pathlib import Path

import pytest

from compresso.frontend._job import JobResult
from compresso.frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractPlan,
    plan_archive,
)


class TestArchiveOptions:
    """Test the ArchiveOptions dataclass."""

    def test_archive_options_default(self):
        """Test creating ArchiveOptions with defaults."""
        opts = ArchiveOptions()

        assert opts.format == "tar.zst"
        assert opts.compression_level is None
        assert opts.preserve_permissions is True

    def test_archive_options_with_format(self):
        """Test creating ArchiveOptions with a format and level."""
        opts = ArchiveOptions(format="tar.gz", compression_level=6)

        assert opts.format == "tar.gz"
        assert opts.compression_level == 6


class TestPlanArchive:
    """Test the plan_archive planner."""

    def test_plan_archive_valid(self, sample_text_file: Path, temp_dir: Path):
        """Test that a plan over existing sources can run and sums input size."""
        out = temp_dir / "out.tar.zst"
        plan = plan_archive([sample_text_file], out)

        assert isinstance(plan, ArchivePlan)
        assert plan.can_run is True
        assert plan.reason_if_unavailable is None
        assert plan.entry_count == 1
        assert plan.total_input_size == sample_text_file.stat().st_size

    def test_plan_archive_no_sources(self, temp_dir: Path):
        """Test that a plan with no sources cannot run."""
        plan = plan_archive([], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None

    def test_plan_archive_missing_source(self, temp_dir: Path):
        """Test that a plan referencing a missing source cannot run."""
        plan = plan_archive([temp_dir / "nope.txt"], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        if plan.reason_if_unavailable is not None:
            assert "does not exist" in plan.reason_if_unavailable

    def test_plan_archive_non_archive_format(
        self, sample_text_file: Path, temp_dir: Path
    ):
        """Test that a single-file codec format cannot be used to build an archive."""
        opts = ArchiveOptions(format="gz")
        plan = plan_archive([sample_text_file], temp_dir / "out.gz", opts)

        assert plan.can_run is False
        if plan.reason_if_unavailable is not None:
            assert "does not support archives" in plan.reason_if_unavailable


class TestArchiveJob:
    """Test the ArchiveJob class."""

    def test_from_paths_builds_plan(self, sample_text_file: Path, temp_dir: Path):
        """Test that from_paths runs the planner and exposes the plan."""
        job = ArchiveJob.from_paths([sample_text_file], temp_dir / "out.tar.zst")

        assert isinstance(job.plan, ArchivePlan)
        assert job.plan.can_run is True

    def test_run_on_unavailable_plan_returns_failed_result(self, temp_dir: Path):
        """Test that run() never raises: an unrunnable plan yields a failed JobResult."""
        job = ArchiveJob.from_paths([], temp_dir / "out.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False
        assert result.error is not None


class TestExtractJob:
    """Test the ExtractJob class."""

    def test_from_archive_missing_returns_unavailable_plan(self, temp_dir: Path):
        """Test that a missing archive produces a plan that cannot run."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")

        assert isinstance(job.plan, ExtractPlan)
        assert job.plan.can_run is False

    def test_run_on_unavailable_plan_returns_failed_result(self, temp_dir: Path):
        """Test that run() never raises on a missing archive."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False


class TestArchiveRoundTrip:
    """Test end-to-end archive -> extract round-trip."""

    def test_round_trip(self, temp_dir: Path, monkeypatch):
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

    def test_directory_round_trip(self, temp_dir: Path, monkeypatch):
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
    def test_rejects_absolute_entry(self, temp_dir: Path, entry_name: str):
        """Test that an absolute or drive-qualified entry name fails extraction."""
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, entry_name)

        result = ExtractJob.from_archive(archive_path, temp_dir / "out").run()

        assert result.ok is False
        assert "absolute path" in str(result.error)

    @pytest.mark.parametrize("entry_name", ["../escape.txt", "sub/../../escape.txt"])
    def test_rejects_parent_traversal(self, temp_dir: Path, entry_name: str):
        """Test that an entry climbing out of the output directory is refused."""
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, entry_name)

        result = ExtractJob.from_archive(archive_path, temp_dir / "out").run()

        assert result.ok is False
        assert "traversal" in str(result.error)
        assert not (temp_dir / "escape.txt").exists()

    def test_backslash_traversal_stays_contained(self, temp_dir: Path):
        """Test that a backslash-separated traversal cannot escape on any platform.

        On Windows `..\\..\\escape.txt` is a real traversal and is refused; on
        POSIX it is an oddly-named file; both outcomes are safe, so assert
        containment rather than a specific verdict.
        """
        archive_path = temp_dir / "evil.tar"
        _tar_with_entry(archive_path, "..\\..\\escape.txt")
        out_dir = temp_dir / "out"

        ExtractJob.from_archive(archive_path, out_dir).run()

        assert not (temp_dir / "escape.txt").exists()
        assert not (temp_dir.parent / "escape.txt").exists()

    @pytest.mark.parametrize(
        "entry_name", ["notes.txt:evil.exe", "notes.txt:evil.exe:$DATA"]
    )
    def test_alternate_data_stream_writes_no_stream(
        self, temp_dir: Path, entry_name: str
    ):
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

    def test_accepts_nested_entry(self, temp_dir: Path):
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

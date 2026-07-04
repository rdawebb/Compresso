"""Tests for the frontend archive API module."""

from pathlib import Path

from compresso.frontend._job import JobResult
from compresso.frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractPlan,
    plan_archive,
    plan_extraction,
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
        """A plan over existing sources can run and sums input size."""
        out = temp_dir / "out.tar.zst"
        plan = plan_archive([sample_text_file], out)

        assert isinstance(plan, ArchivePlan)
        assert plan.can_run is True
        assert plan.reason_if_unavailable is None
        assert plan.entry_count == 1
        assert plan.total_input_size == sample_text_file.stat().st_size

    def test_plan_archive_no_sources(self, temp_dir: Path):
        """A plan with no sources cannot run."""
        plan = plan_archive([], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        assert plan.reason_if_unavailable is not None

    def test_plan_archive_missing_source(self, temp_dir: Path):
        """A plan referencing a missing source cannot run."""
        plan = plan_archive([temp_dir / "nope.txt"], temp_dir / "out.tar.zst")

        assert plan.can_run is False
        assert "does not exist" in plan.reason_if_unavailable

    def test_plan_archive_non_archive_format(
        self, sample_text_file: Path, temp_dir: Path
    ):
        """A single-file codec format cannot be used to build an archive."""
        opts = ArchiveOptions(format="gz")
        plan = plan_archive([sample_text_file], temp_dir / "out.gz", opts)

        assert plan.can_run is False
        assert "does not support archives" in plan.reason_if_unavailable


class TestArchiveJob:
    """Test the ArchiveJob class."""

    def test_from_paths_builds_plan(self, sample_text_file: Path, temp_dir: Path):
        """from_paths runs the planner and exposes the plan."""
        job = ArchiveJob.from_paths([sample_text_file], temp_dir / "out.tar.zst")

        assert isinstance(job.plan, ArchivePlan)
        assert job.plan.can_run is True

    def test_run_on_unavailable_plan_returns_failed_result(self, temp_dir: Path):
        """run() never raises: an unrunnable plan yields a failed JobResult."""
        job = ArchiveJob.from_paths([], temp_dir / "out.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False
        assert result.error is not None


class TestExtractJob:
    """Test the ExtractJob class."""

    def test_from_archive_missing_returns_unavailable_plan(self, temp_dir: Path):
        """A missing archive produces a plan that cannot run."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")

        assert isinstance(job.plan, ExtractPlan)
        assert job.plan.can_run is False

    def test_run_on_unavailable_plan_returns_failed_result(self, temp_dir: Path):
        """run() never raises on a missing archive."""
        job = ExtractJob.from_archive(temp_dir / "missing.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False


class TestArchiveRoundTrip:
    """End-to-end archive -> extract round-trip."""

    def test_round_trip(self, temp_dir: Path, monkeypatch):
        """Archiving then extracting restores the original file contents.

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
        """Archiving a directory preserves its nested structure on extraction."""
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

        extract_result = ExtractJob.from_archive(
            archive_path, temp_dir / "out"
        ).run()
        assert extract_result.ok, extract_result.error

        assert (temp_dir / "out" / "tree" / "top.txt").read_bytes() == b"top level"
        assert (
            temp_dir / "out" / "tree" / "deep" / "leaf.txt"
        ).read_bytes() == b"leaf content"

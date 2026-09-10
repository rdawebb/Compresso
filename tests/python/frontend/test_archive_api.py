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

    def test_non_ascii_names_round_trip(self, temp_dir: Path, monkeypatch):
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


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlink creation needs privilege on Windows"
)
class TestArchivingSymlinks:
    """Test that a symlink is archived as a symlink, not as what it points at.

    Written against an uncompressed tar read back by `tarfile`, because the
    listing API reports entry names only and cannot show an entry's type.
    """

    def test_symlink_is_stored_as_a_link(self, temp_dir: Path, monkeypatch):
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

    def test_symlink_loop_does_not_recurse(self, temp_dir: Path, monkeypatch):
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

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlink creation needs privilege on Windows"
    )
    def test_rejects_entry_writing_through_a_symlinked_component(self, temp_dir: Path):
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
    def test_rejects_entry_writing_through_a_junction(self, temp_dir: Path):
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


class TestExtractionSizeCap:
    """Test that `max_total_size` bounds what an archive can write."""

    def test_no_cap_by_default(self, temp_dir: Path):
        """Test that the shipped default places no limit on extracted bytes."""
        archive_path = temp_dir / "big.tar"
        _tar_with_entries(archive_path, [_file_entry("big.bin", 200_000)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "big.bin").stat().st_size == 200_000

    def test_refuses_archive_over_the_cap(self, temp_dir: Path):
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

    def test_cap_applies_to_the_total_not_each_entry(self, temp_dir: Path):
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

    def test_entry_under_the_cap_extracts(self, temp_dir: Path):
        """Test that the cap does not over-reject an archive that fits."""
        archive_path = temp_dir / "small.tar"
        _tar_with_entries(archive_path, [_file_entry("small.bin", 500)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(max_total_size=1000)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "small.bin").stat().st_size == 500

    def test_understated_entry_size_is_still_capped(self, temp_dir: Path):
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

    def test_refuses_entry_past_the_depth_limit(self, temp_dir: Path):
        """Test that an entry nested past the default limit is refused."""
        archive_path = temp_dir / "deep.tar"
        name = "/".join(f"d{i}" for i in range(40)) + "/leaf.txt"
        _tar_with_entries(archive_path, [_file_entry(name)])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert "max depth" in str(result.error)
        assert list(out_dir.iterdir()) == []

    def test_accepts_entry_within_the_depth_limit(self, temp_dir: Path):
        """Test that an entry just inside the limit still extracts."""
        archive_path = temp_dir / "deep.tar"
        parts = [f"d{i}" for i in range(31)]
        _tar_with_entries(archive_path, [_file_entry("/".join([*parts, "leaf.txt"]))])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert out_dir.joinpath(*parts, "leaf.txt").exists()

    def test_depth_limit_is_configurable(self, temp_dir: Path):
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
    """Test the three `overwrite` modes against an existing destination."""

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

    def test_refuses_existing_file_by_default(self, temp_dir: Path):
        """Test that the default policy refuses to touch an existing file."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_skip_leaves_the_existing_file(self, temp_dir: Path):
        """Test that `skip` succeeds without replacing what is already there."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite="skip")
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"original"

    def test_overwrite_replaces_the_existing_file(self, temp_dir: Path):
        """Test that `overwrite` replaces the file's contents."""
        archive_path, out_dir = self._archive_and_stale_output(temp_dir)

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(overwrite="overwrite")
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").read_bytes() == b"xxxxx"

    def test_unknown_mode_is_an_unavailable_plan(self, temp_dir: Path):
        """Test that a mode outside the three names never reaches the C layer."""
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

    def test_restores_mtime_by_default(self, temp_dir: Path):
        """Test that the entry's modification time survives extraction."""
        archive_path = temp_dir / "meta.tar"
        self._archive_with_metadata(archive_path, mtime=1_000_000_000, mode=0o644)
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mtime == pytest.approx(
            1_000_000_000, abs=2
        )

    def test_preserve_timestamps_off_uses_the_current_time(self, temp_dir: Path):
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
    def test_restores_mode_by_default(self, temp_dir: Path):
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
    def test_preserve_permissions_off_leaves_the_created_mode(self, temp_dir: Path):
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

    def test_refuses_fifo_entry(self, temp_dir: Path):
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

    def test_later_bad_entry_blocks_the_earlier_good_one(self, temp_dir: Path):
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

    def test_parent_segment_check_allows_a_leading_dot_name(self, temp_dir: Path):
        """Test that `..data` is a filename, not a traversal."""
        archive_path = temp_dir / "dots.tar"
        _tar_with_entries(archive_path, [_file_entry("..data")])
        out_dir = temp_dir / "out"

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        assert (out_dir / "..data").read_bytes() == b"xxxxx"

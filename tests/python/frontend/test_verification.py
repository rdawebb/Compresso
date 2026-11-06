"""Tests for the frontend integrity check."""

import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from compresso import CancelToken, TrailingDataWarning
from compresso.frontend.api import (
    CompressionJob,
    CompressionOptions,
    VerificationJob,
    plan_verification,
)
from compresso.frontend.archive_api import ArchiveJob, ArchiveOptions

from ..helpers import Recorder, flip_middle_byte

CODECS = ["gz", "bz2", "xz", "zst", "lz4", "sz"]
ARCHIVES = ["tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4", "zip"]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A file holding enough incompressible data to survive one flipped byte."""
    path = tmp_path / "source.bin"
    path.write_bytes(os.urandom(64 * 1024))

    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A directory of two files."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.bin").write_bytes(os.urandom(32 * 1024))
    (root / "b.bin").write_bytes(os.urandom(32 * 1024))

    return root


def compressed(source: Path, suffix: str, algo: str | None = None) -> Path:
    """Compress `source` beside itself."""
    output = source.with_suffix(f".{suffix}")
    fmt = None if suffix == "comp" else suffix
    options = CompressionOptions(algo=algo, format=fmt)
    result = CompressionJob.from_file(source, output, options).run()
    assert result.ok, result.error

    return output


def archived(tree: Path, fmt: str) -> Path:
    """Archive `tree` beside itself."""
    output = tree.with_name(f"out.{fmt}")
    result = ArchiveJob.from_paths([tree], output, ArchiveOptions(format=fmt)).run()
    assert result.ok, result.error

    return output


def cut_short(path: Path) -> None:
    """Drop the last third of `path`."""
    data = path.read_bytes()
    path.write_bytes(data[: len(data) * 2 // 3])


def assert_damage_caught(path: Path) -> None:
    """Assert that `path` passes, then fails once flipped or cut short."""
    good = path.read_bytes()
    assert VerificationJob.from_file(path).run().ok

    flip_middle_byte(path)
    assert not VerificationJob.from_file(path).run().ok

    path.write_bytes(good)
    cut_short(path)
    assert not VerificationJob.from_file(path).run().ok


class TestSingleFile:
    """Test standalone codecs and the `.comp` container."""

    @pytest.mark.parametrize("suffix", CODECS)
    def test_each_codec(self, source: Path, suffix: str) -> None:
        """Test that a good file passes, and each kind of damage fails it."""
        assert_damage_caught(compressed(source, suffix))

    def test_each_comp_algorithm(self, source: Path, compression_algo: str) -> None:
        """Test that `.comp` damage is caught through every backend."""
        assert_damage_caught(compressed(source, "comp", compression_algo))

    def test_trailing_data_only_warns_here(self, source: Path) -> None:
        """Test that the job leaves the verdict on trailing data to its caller."""
        path = compressed(source, "gz")
        path.write_bytes(path.read_bytes() + b"not gzip data")

        with pytest.warns(TrailingDataWarning):
            assert VerificationJob.from_file(path).run().ok


class TestArchives:
    """Test archives, whose entries are decoded too."""

    @pytest.mark.parametrize("fmt", ARCHIVES)
    def test_each_format(self, tree: Path, fmt: str) -> None:
        """Test that a good archive passes, and each kind of damage fails it."""
        assert_damage_caught(archived(tree, fmt))

    def test_plain_tar_has_no_data_checksum(self, tree: Path) -> None:
        """Test that only what tar can detect fails it, unlike every other format."""
        path = archived(tree, "tar")
        good = path.read_bytes()
        with tarfile.open(path) as tar:
            data_at = tar.getmembers()[-1].offset_data

        damaged = bytearray(good)
        damaged[data_at] ^= 0xFF
        path.write_bytes(damaged)
        assert VerificationJob.from_file(path).run().ok

        # A header has a checksum of its own
        damaged = bytearray(good)
        damaged[data_at - 512] ^= 0xFF
        path.write_bytes(damaged)
        assert not VerificationJob.from_file(path).run().ok

        path.write_bytes(good)
        cut_short(path)
        assert not VerificationJob.from_file(path).run().ok

    def test_plain_tar_cut_between_entries_passes(self, tree: Path) -> None:
        """Test that a cut leaving whole entries behind passes, as bsdtar has it."""
        path = archived(tree, "tar")
        with tarfile.open(path) as tar:
            last = tar.getmembers()[-1]

        path.write_bytes(path.read_bytes()[: last.offset])

        assert VerificationJob.from_file(path).run().ok

    def test_stored_zip_entry_checks_its_crc(self, tmp_path: Path) -> None:
        """Test that an uncompressed entry, which no decoder would catch, fails."""
        path = tmp_path / "stored.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("data", os.urandom(4096))

        flip_middle_byte(path)

        assert "CRC" in str(VerificationJob.from_file(path).run().error)

    def test_zip_symlink_target_checks_its_crc(self, tmp_path: Path) -> None:
        """Test that a link, whose data is read with its header, is checked too."""
        path = tmp_path / "link.zip"
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # Unix, or the mode below would be ignored
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, "some-link-target")

        assert VerificationJob.from_file(path).run().ok

        data = path.read_bytes()
        path.write_bytes(data.replace(b"some-link-target", b"sOme-link-target"))

        assert "CRC" in str(VerificationJob.from_file(path).run().error)

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_plain_archive_progress_counts_its_data(self, tree: Path, fmt: str) -> None:
        """Test that the total is what the entries hold, not the archive's size."""
        recorder = Recorder()

        result = VerificationJob.from_file(archived(tree, fmt)).run(progress=recorder)

        assert result.ok
        assert recorder.calls[-1] == (64 * 1024, 64 * 1024)

    def test_compressed_archive_progress_counts_its_input(self, tree: Path) -> None:
        """Test that a codec stage reports the compressed bytes it consumes."""
        path = archived(tree, "tar.gz")
        recorder = Recorder()

        VerificationJob.from_file(path).run(progress=recorder)

        assert recorder.calls[-1] == (path.stat().st_size, path.stat().st_size)


class TestRun:
    """Test what holds for every input."""

    @pytest.mark.parametrize("damaged", [False, True])
    @pytest.mark.parametrize("build", ["gz", "comp", "tar.gz", "zip"])
    def test_writes_nothing(
        self, source: Path, tree: Path, build: str, damaged: bool
    ) -> None:
        """Test that the input and its directory are left as they were."""
        is_archive = build in ARCHIVES
        path = archived(tree, build) if is_archive else compressed(source, build)
        if damaged:
            flip_middle_byte(path)

        before = sorted(path.parent.rglob("*"))
        content = path.read_bytes()

        result = VerificationJob.from_file(path).run()

        assert result.ok is not damaged
        assert sorted(path.parent.rglob("*")) == before
        assert path.read_bytes() == content

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    def test_cancelled_archive(self, tree: Path, fmt: str) -> None:
        """Test that a cancel is reported as one rather than as damage."""
        token = CancelToken()
        token.cancel()

        result = VerificationJob.from_file(archived(tree, fmt)).run(cancel=token)

        assert result.cancelled
        assert result.error is None

    def test_unrecognised_file_cannot_run(self, tmp_path: Path) -> None:
        """Test that a file of no known format is refused at the plan."""
        path = tmp_path / "notes.txt"
        path.write_text("just some text")

        plan = plan_verification(path)

        assert not plan.can_run
        assert not VerificationJob(plan).run().ok

    def test_missing_file_cannot_run(self, tmp_path: Path) -> None:
        """Test that a missing input surfaces as an unavailable plan."""
        assert not plan_verification(tmp_path / "nope.gz").can_run

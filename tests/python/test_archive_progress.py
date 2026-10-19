"""Tests for archive progress reporting and cancellation."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from compresso._core import (
    Cancelled,
    CancelToken,
    create_archive,
    extract_archive,
)
from compresso.frontend.archive_api import ArchiveJob, ArchiveOptions, ExtractJob

from .helpers import Recorder

# Incompressible, so the compressed archive stays above the 1 MiB report floor
ENTRY_COUNT = 4
ENTRY_SIZE = 2 * 1024 * 1024


@pytest.fixture(scope="module")
def source_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory of files large enough to produce several progress reports.

    Shared by the whole module, so tests must only read it.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        The path to the source tree directory.
    """
    root = tmp_path_factory.mktemp("archive_progress") / "src"
    root.mkdir()
    for i in range(ENTRY_COUNT):
        (root / f"f{i}.bin").write_bytes(os.urandom(ENTRY_SIZE))

    return root


class TestArchiveCreationProgress:
    """Tests progress while writing an archive."""

    def test_progress_is_incremental_monotonic_and_completes(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that every format reports during the job, rising to the total.

        For zip the only real progress comes from libzip's own callback
        during zip_close.
        """
        fmt, ext = archive_format
        recorder = Recorder()

        create_archive(
            str(tmp_path / f"out{ext}"),
            fmt,
            [str(source_tree)],
            progress=recorder,
        )

        assert len(recorder.calls) > 1
        recorder.assert_monotonic()
        recorder.assert_finished()

    def test_compressed_tar_is_never_on_disk_uncompressed(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that tar.zst is compressed as it is written."""
        temps: list[int] = []

        def count_temps(done: int, total: int) -> None:
            """Record how many temps exist at this report."""
            temps.append(len(list(tmp_path.glob(".compresso-*"))))

        create_archive(
            str(tmp_path / "out.tar.zst"),
            "tar.zst",
            [str(source_tree)],
            progress=count_temps,
        )

        assert temps
        assert max(temps) == 1

    def test_single_stage_total_matches_the_input(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that plain tar reads each input once, so the total is the input size."""
        recorder = Recorder()
        create_archive(
            str(tmp_path / "out.tar"),
            "tar",
            [str(source_tree)],
            progress=recorder,
        )

        assert recorder.calls[-1][1] == ENTRY_COUNT * ENTRY_SIZE


class TestArchiveExtractionProgress:
    """Tests progress while extracting an archive."""

    def test_progress_completes_and_round_trips(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that every format reports rising byte counts ending at the total.

        Watching the job must not change what it produces, so the extracted
        files are checked against the originals too.
        """
        fmt, ext = archive_format
        archive = tmp_path / f"in{ext}"
        dest = tmp_path / f"out{ext}"
        create_archive(str(archive), fmt, [str(source_tree)], progress=Recorder())

        recorder = Recorder()
        extract_archive(str(archive), str(dest), [], progress=recorder)

        assert len(recorder.calls) > 1
        recorder.assert_monotonic()
        recorder.assert_finished()
        for original in sorted(source_tree.iterdir()):
            restored = dest / source_tree.name / original.name
            assert restored.read_bytes() == original.read_bytes()

    def test_validation_pass_reports_nothing(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that only the pass that writes files reports progress.

        Extraction walks the archive twice: once to validate, once to write
        """
        archive = tmp_path / "in.tar"
        create_archive(str(archive), "tar", [str(source_tree)])

        recorder = Recorder()
        extract_archive(str(archive), str(tmp_path / "out"), [], progress=recorder)

        assert recorder.calls[-1][1] == ENTRY_COUNT * ENTRY_SIZE


class TestArchiveCancellation:
    """Tests stopping an archive job through a CancelToken."""

    def test_cancel_during_creation(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that cancelling aborts and leaves no archive behind."""
        fmt, ext = archive_format
        output = tmp_path / f"cancelled{ext}"
        token = CancelToken()

        with pytest.raises(Cancelled):
            create_archive(
                str(output),
                fmt,
                [str(source_tree)],
                progress=lambda done, total: token.cancel(),
                cancel=token,
            )

        assert not output.exists()

    def test_cancel_before_creation_starts(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a token cancelled up front stops the job immediately."""
        fmt, ext = archive_format
        output = tmp_path / f"never{ext}"
        token = CancelToken()
        token.cancel()

        with pytest.raises(Cancelled):
            create_archive(str(output), fmt, [str(source_tree)], cancel=token)

        assert not output.exists()

    def test_cancel_lands_during_the_walk(
        self, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that a cancel lands while walking a tree with no file data."""
        fmt, ext = archive_format
        source = tmp_path / "dirs"
        (source / "a" / "b").mkdir(parents=True)
        output = tmp_path / f"never{ext}"
        token = CancelToken()
        token.cancel()

        with pytest.raises(Cancelled):
            create_archive(str(output), fmt, [str(source)], cancel=token)

        assert not output.exists()

    def test_cancel_during_extraction(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that cancelling extraction aborts with Cancelled."""
        fmt, ext = archive_format
        archive = tmp_path / f"in{ext}"
        create_archive(str(archive), fmt, [str(source_tree)])

        token = CancelToken()
        with pytest.raises(Cancelled):
            extract_archive(
                str(archive),
                str(tmp_path / f"out{ext}"),
                [],
                progress=lambda done, total: token.cancel(),
                cancel=token,
            )

    # tar.zst counts compressed bytes; random data keeps them near the raw size
    @pytest.mark.parametrize("fmt", ["tar", "tar.zst"])
    def test_cancelled_extraction_leaves_no_truncated_file(
        self, source_tree: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that entries already extracted are kept, but a half-written one is not.

        Unlike a failure, a cancel undoes only the entry in flight.
        """
        archive = tmp_path / f"in.{fmt}"
        dest = tmp_path / "out"
        create_archive(str(archive), fmt, [str(source_tree)])

        # Stop midway through the second entry, so at least one has completed
        # and one is genuinely in flight
        token = CancelToken()

        def cancel_partway(done: int, total: int) -> None:
            """Cancel on the first report inside the second entry."""
            if done > ENTRY_SIZE:
                token.cancel()

        with pytest.raises(Cancelled):
            extract_archive(
                str(archive), str(dest), [], progress=cancel_partway, cancel=token
            )

        extracted = sorted((dest / source_tree.name).iterdir())
        assert extracted, "expected the completed entries to be kept"
        assert len(extracted) < ENTRY_COUNT, "expected the job to have stopped early"

        originals = {p.name: p.read_bytes() for p in source_tree.iterdir()}
        for restored in extracted:
            assert restored.read_bytes() == originals[restored.name], (
                f"{restored.name} was left truncated"
            )

    def test_cancel_from_another_thread(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a token can be cancelled by a thread other than the one archiving."""
        output = tmp_path / "threaded.tar.zst"
        token = CancelToken()
        started = threading.Event()

        def watcher() -> None:
            """Cancel the token after the archive has started."""
            started.wait(timeout=10)
            token.cancel()

        thread = threading.Thread(target=watcher)
        thread.start()
        try:
            with pytest.raises(Cancelled):
                create_archive(
                    str(output),
                    "tar.zst",
                    [str(source_tree)],
                    9,
                    progress=lambda done, total: started.set(),
                    cancel=token,
                )
        finally:
            started.set()
            thread.join(timeout=10)

        assert not output.exists()

    # Threaded codecs buffer the input, so most of their work follows it
    @pytest.mark.parametrize("fmt", ["tar.zst", "tar.xz"])
    def test_cancel_after_the_last_source_byte(
        self, source_tree: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a cancel still lands while the codec flushes.

        The last report inside the data loop is at the total, after which no
        source bytes are left to poll from.
        """
        output = tmp_path / f"flushing.{fmt}"
        token = CancelToken()

        def cancel_at_end(done: int, total: int) -> None:
            """Cancel once every source byte is counted."""
            if done >= total:
                token.cancel()

        with pytest.raises(Cancelled):
            create_archive(
                str(output),
                fmt,
                [str(source_tree)],
                progress=cancel_at_end,
                cancel=token,
            )

        assert not output.exists()

    def test_uncancelled_token_does_not_interfere(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that passing a token that is never cancelled completes normally."""
        fmt, ext = archive_format
        output = tmp_path / f"fine{ext}"

        create_archive(str(output), fmt, [str(source_tree)], cancel=CancelToken())

        assert output.stat().st_size > 0


class TestArchiveCallbackErrors:
    """Tests archive progress callback error handling behaviour."""

    def test_exception_from_callback_propagates(
        self, source_tree: Path, tmp_path: Path, archive_format: tuple[str, str]
    ) -> None:
        """Test that the callback's own exception wins over Cancelled or a backend error."""
        fmt, ext = archive_format

        class Boom(Exception):
            """Raised by the callback to simulate an error."""

        def explode(done: int, total: int) -> None:
            """Simulates a callback that raises an error."""
            raise Boom("callback failed")

        with pytest.raises(Boom, match="callback failed"):
            create_archive(
                str(tmp_path / f"boom{ext}"),
                fmt,
                [str(source_tree)],
                progress=explode,
            )


class TestArchiveJobIntegration:
    """Test the frontend archive jobs surface progress and cancellation."""

    def test_archive_job_reports_progress(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that ArchiveJob.run drives the callback with real byte counts."""
        calls: list[tuple[float, int, int]] = []
        job = ArchiveJob.from_paths(
            sources=[source_tree], output=tmp_path / "job.tar.zst"
        )

        result = job.run(progress=lambda f, done, total: calls.append((f, done, total)))

        assert result.ok is True
        assert result.cancelled is False
        assert len(calls) > 1
        assert [done for _, done, _ in calls] == sorted(done for _, done, _ in calls)
        fraction, done, total = calls[-1]
        assert done == total
        assert fraction == pytest.approx(1.0)

    def test_archive_job_reports_cancellation(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a cancelled job comes back flagged, not raising and not an error."""
        output = tmp_path / "job_cancelled.tar.zst"
        token = CancelToken()
        job = ArchiveJob.from_paths(sources=[source_tree], output=output)

        result = job.run(progress=lambda f, done, total: token.cancel(), cancel=token)

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None
        assert not output.exists()

    def test_extract_job_reports_progress(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that ExtractJob.run reports over the bytes it writes."""
        archive = tmp_path / "job.tar"
        ArchiveJob.from_paths(
            sources=[source_tree],
            output=archive,
            options=ArchiveOptions(format="tar"),
        ).run()

        calls: list[tuple[float, int, int]] = []
        job = ExtractJob.from_archive(archive=archive, output_dir=tmp_path / "out")
        result = job.run(progress=lambda f, done, total: calls.append((f, done, total)))

        assert result.ok is True
        assert len(calls) > 1
        _, done, total = calls[-1]
        assert done == total

    def test_extract_job_reports_cancellation(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that a cancelled extraction comes back flagged rather than failed."""
        archive = tmp_path / "job.tar"
        ArchiveJob.from_paths(
            sources=[source_tree],
            output=archive,
            options=ArchiveOptions(format="tar"),
        ).run()

        token = CancelToken()
        job = ExtractJob.from_archive(archive=archive, output_dir=tmp_path / "out")
        result = job.run(progress=lambda f, done, total: token.cancel(), cancel=token)

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None

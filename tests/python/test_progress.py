"""Tests for progress reporting and cancellation (the CoreContext layer)."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

import pytest

from compresso import _core
from compresso._core import Cancelled, CancelToken, compress_file, decompress_file
from compresso.frontend.api import CompressionJob, DecompressionJob

from .helpers import Recorder

# Crosses the 1 MiB report floor (`CTX_MIN_INTERVAL`) several times over
PROGRESS_FILE_SIZE = 4 * 1024 * 1024

# bzip2 and lzma take ~1s each on this payload
BACKENDS = [
    "zlib",
    pytest.param("bzip2", marks=pytest.mark.slow),
    pytest.param("lzma", marks=pytest.mark.slow),
    "zstd",
    "lz4",
    "snappy",
]


def fast_level(algo: str) -> int:
    """The quickest level a backend accepts, for tests where the level is moot.

    Args:
        algo: The backend's name.

    Returns:
        Level 1, or the default for snappy, which has no levels.
    """
    return -1 if algo == "snappy" else 1


@pytest.fixture(scope="module")
def big_compressible_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A file large enough to produce several progress reports when compressed.

    Shared by the whole module, so tests must only read it.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        Path: The path to the big compressible file.
    """
    file_path = tmp_path_factory.mktemp("progress") / "big_compressible.bin"
    chunk = b"compresso progress test payload " * 1024  # 32 KB
    file_path.write_bytes(chunk * (PROGRESS_FILE_SIZE // len(chunk)))

    return file_path


@pytest.fixture(scope="module")
def big_incompressible_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A file that stays large once compressed.

    Progress counts input bytes, so decompression's total is the compressed
    size; random data keeps that above the report-interval floor; shared by
    the whole module, so tests must only read it.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        Path: The path to the big incompressible file.
    """
    file_path = tmp_path_factory.mktemp("progress") / "big_incompressible.bin"
    file_path.write_bytes(os.urandom(PROGRESS_FILE_SIZE))

    return file_path


class TestCompressionProgress:
    """Test progress reporting from _core.compress_file."""

    def test_reports_are_incremental_over_the_input_size(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that progress arrives during the job, out of the source file's size.

        The denominator is known up front rather than guessed.
        """
        recorder = Recorder()
        compress_file(
            str(big_compressible_file),
            str(temp_dir / "out.comp"),
            "zstd",
            "balanced",
            3,
            progress=recorder,
        )

        assert len(recorder.calls) > 1
        expected = big_compressible_file.stat().st_size
        assert all(total == expected for _, total in recorder.calls)

    @pytest.mark.parametrize("algo", BACKENDS)
    def test_progress_is_monotonic_and_completes(
        self, big_compressible_file: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test that every backend reports rising byte counts ending exactly at the total."""
        recorder = Recorder()
        compress_file(
            str(big_compressible_file),
            str(temp_dir / f"{algo}.comp"),
            algo,
            "balanced",
            fast_level(algo),
            progress=recorder,
        )

        recorder.assert_monotonic()
        recorder.assert_finished()

    def test_runs_without_a_callback(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that omitting progress leaves the pre-context behaviour untouched."""
        output = temp_dir / "out.comp"
        compress_file(str(big_compressible_file), str(output), "zstd", "balanced", 3)

        assert output.stat().st_size > 0


class TestDecompressionProgress:
    """Test progress reporting from _core.decompress_file."""

    @pytest.mark.parametrize("algo", BACKENDS)
    def test_progress_is_monotonic_and_completes(
        self, big_incompressible_file: Path, temp_dir: Path, algo: str
    ) -> None:
        """Test that decompression reports too, counting compressed bytes consumed."""
        compressed = temp_dir / f"{algo}.comp"
        restored = temp_dir / f"{algo}.out"
        compress_file(
            str(big_incompressible_file),
            str(compressed),
            algo,
            "balanced",
            fast_level(algo),
        )

        recorder = Recorder()
        decompress_file(str(compressed), str(restored), algo, progress=recorder)

        recorder.assert_monotonic()
        recorder.assert_finished()
        assert len(recorder.calls) > 1
        assert restored.read_bytes() == big_incompressible_file.read_bytes()


class TestStandaloneProgress:
    """Test progress reporting from the standalone (.gz/.bz2/.xz/.zst/.lz4) codecs."""

    @pytest.mark.parametrize(
        ("fmt", "ext"),
        [
            ("gzip", ".gz"),
            pytest.param("bzip2", ".bz2", marks=pytest.mark.slow),
            pytest.param("xz", ".xz", marks=pytest.mark.slow),
            ("zstd", ".zst"),
            ("lz4", ".lz4"),
        ],
    )
    def test_round_trip_reports_progress(
        self, big_incompressible_file: Path, temp_dir: Path, fmt: str, ext: str
    ) -> None:
        """Test that both directions report, and the data survives the round trip."""
        compressed = temp_dir / f"standalone{ext}"
        restored = temp_dir / f"restored{ext}.out"

        compressing = Recorder()
        _core.compress_standalone(
            str(big_incompressible_file),
            str(compressed),
            fmt,
            1,
            progress=compressing,
        )

        decompressing = Recorder()
        _core.decompress_standalone(
            str(compressed), str(restored), fmt, progress=decompressing
        )

        for recorder in (compressing, decompressing):
            recorder.assert_monotonic()
            recorder.assert_finished()

        assert restored.read_bytes() == big_incompressible_file.read_bytes()


class TestCancelToken:
    """Test the CancelToken type itself."""

    def test_starts_uncancelled(self) -> None:
        """Test that a fresh token has not been cancelled."""
        assert CancelToken().cancelled is False

    def test_cancel_sets_the_flag(self) -> None:
        """Test that cancel() is visible through the property."""
        token = CancelToken()
        token.cancel()
        assert token.cancelled is True

    def test_cancel_is_idempotent(self) -> None:
        """Test that cancelling twice is harmless."""
        token = CancelToken()
        token.cancel()
        token.cancel()
        assert token.cancelled is True


class TestCancellation:
    """Test stopping a running job through a CancelToken."""

    def test_cancel_leaves_no_partial_file(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that the half-written destination is removed rather than left behind."""
        output = temp_dir / "cancelled.comp"
        token = CancelToken()

        with pytest.raises(Cancelled):
            compress_file(
                str(big_compressible_file),
                str(output),
                "zstd",
                "balanced",
                3,
                progress=lambda done, total: token.cancel(),
                cancel=token,
            )

        assert not output.exists()

    def test_token_cancelled_before_the_call(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that a token cancelled up front stops the job immediately.

        No progress callback is passed, so this also shows cancellation does
        not depend on progress being requested.
        """
        output = temp_dir / "never.comp"
        token = CancelToken()
        token.cancel()

        with pytest.raises(Cancelled):
            compress_file(
                str(big_compressible_file),
                str(output),
                "zstd",
                "balanced",
                3,
                cancel=token,
            )

        assert not output.exists()

    def test_uncancelled_token_does_not_interfere(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that passing a token that is never cancelled completes normally."""
        output = temp_dir / "fine.comp"
        compress_file(
            str(big_compressible_file),
            str(output),
            "zstd",
            "balanced",
            3,
            cancel=CancelToken(),
        )

        assert output.stat().st_size > 0

    def test_cancel_from_another_thread(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that a token can be cancelled by a thread other than the one compressing."""
        output = temp_dir / "threaded.comp"
        token = CancelToken()
        started = threading.Event()

        def watcher() -> None:
            started.wait(timeout=5)
            token.cancel()

        thread = threading.Thread(target=watcher)
        thread.start()
        try:
            with pytest.raises(Cancelled):
                compress_file(
                    str(big_compressible_file),
                    str(output),
                    "bzip2",
                    "balanced",
                    9,
                    progress=lambda done, total: started.set(),
                    cancel=token,
                )
        finally:
            started.set()
            thread.join(timeout=5)


class TestCallbackErrors:
    """Test progress callback error behaviour."""

    def test_exception_from_callback_propagates(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that the callback's own exception wins over any backend or Cancelled error."""

        class Boom(Exception):
            """Mock exception."""

        def explode(done: int, total: int) -> None:
            """Raises Boom to simulate a failed callback."""
            raise Boom("callback failed")

        with pytest.raises(Boom, match="callback failed"):
            compress_file(
                str(big_compressible_file),
                str(temp_dir / "boom.comp"),
                "zstd",
                "balanced",
                3,
                progress=explode,
            )

    def test_failed_callback_leaves_no_partial_file(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that a callback failure cleans up like any other failure."""
        output = temp_dir / "boom.comp"

        def explode(done: int, total: int) -> None:
            """Raises RuntimeError to simulate a failed callback."""
            raise RuntimeError("callback failed")

        with pytest.raises(RuntimeError):
            compress_file(
                str(big_compressible_file),
                str(output),
                "zstd",
                "balanced",
                3,
                progress=explode,
            )

        assert not output.exists()

    def test_non_callable_progress_is_rejected(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a bad progress argument fails fast rather than mid-compression."""
        with pytest.raises(TypeError, match="progress must be callable"):
            compress_file(
                str(sample_text_file),
                str(temp_dir / "x.comp"),
                "zstd",
                "balanced",
                3,
                progress=42,  # ty: ignore[invalid-argument-type] - the point of the test
            )

    def test_wrong_cancel_type_is_rejected(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that cancel= only accepts a CancelToken."""
        with pytest.raises(TypeError, match="cancel must be a CancelToken"):
            compress_file(
                str(sample_text_file),
                str(temp_dir / "x.comp"),
                "zstd",
                "balanced",
                3,
                cancel="please stop",  # ty: ignore[invalid-argument-type] - the point of the test
            )


class TestSignalHandling:
    """Test that pending signals are run mid-compression, not just when the call returns."""

    def test_signal_raised_during_callback_aborts(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that a signal pending when the bridge runs interrupts the compression."""
        output = temp_dir / "interrupted.comp"

        def raise_sigint(done: int, total: int) -> None:
            """Raise a SIGINT signal to interrupt the compression."""
            signal.raise_signal(signal.SIGINT)

        with pytest.raises(KeyboardInterrupt):
            compress_file(
                str(big_compressible_file),
                str(output),
                "zstd",
                "balanced",
                3,
                progress=raise_sigint,
            )

        assert not output.exists()

    @pytest.mark.skipif(
        not hasattr(signal, "setitimer"), reason="setitimer is POSIX-only"
    )
    def test_signal_aborts_without_a_progress_callback(
        self, big_incompressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that signals are checked even when no progress was requested."""
        output = temp_dir / "alarmed.comp"

        class Alarm(Exception):
            """Mock alarm exception."""

        def on_alarm(signum: int, frame: object) -> None:
            """Raised when the alarm timer expires."""
            raise Alarm

        previous = signal.signal(signal.SIGALRM, on_alarm)
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.05)
            with pytest.raises(Alarm):
                # bzip2 at level 9 on incompressible data is slow enough that
                # the timer lands well before the job would finish
                compress_file(
                    str(big_incompressible_file),
                    str(output),
                    "bzip2",
                    "balanced",
                    9,
                )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

        assert not output.exists()


class TestConcurrency:
    """Test that two jobs at once must not share progress or cancellation state."""

    def test_parallel_jobs_keep_separate_contexts(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that cancelling one job leaves the other to finish."""
        finished: dict[str, object] = {}

        def run_cancelled() -> None:
            """Run a job that is cancelled before completion."""
            token = CancelToken()
            try:
                compress_file(
                    str(big_compressible_file),
                    str(temp_dir / "a.comp"),
                    "zstd",
                    "balanced",
                    3,
                    progress=lambda done, total: token.cancel(),
                    cancel=token,
                )
            except Cancelled:
                finished["a"] = "cancelled"

        def run_to_completion() -> None:
            """Run a job that completes without cancellation."""
            recorder = Recorder()
            compress_file(
                str(big_compressible_file),
                str(temp_dir / "b.comp"),
                "zstd",
                "balanced",
                3,
                progress=recorder,
                cancel=CancelToken(),
            )
            finished["b"] = recorder

        threads = [
            threading.Thread(target=run_cancelled),
            threading.Thread(target=run_to_completion),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert finished["a"] == "cancelled"
        assert not (temp_dir / "a.comp").exists()

        recorder = finished["b"]
        assert isinstance(recorder, Recorder)
        recorder.assert_monotonic()
        recorder.assert_finished()
        assert (temp_dir / "b.comp").stat().st_size > 0


class TestJobIntegration:
    """Test that the frontend jobs surface progress and cancellation without raising."""

    def test_compression_job_reports_progress(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that CompressionJob.run drives the callback with real byte counts."""
        calls: list[tuple[float, int, int]] = []
        job = CompressionJob.from_file(
            src=big_compressible_file, dest=temp_dir / "job.comp"
        )

        result = job.run(progress=lambda f, done, total: calls.append((f, done, total)))

        assert result.ok is True
        assert result.cancelled is False
        assert len(calls) > 1
        assert [done for _, done, _ in calls] == sorted(done for _, done, _ in calls)
        fraction, done, total = calls[-1]
        assert done == total
        assert fraction == pytest.approx(1.0)

    def test_compression_job_reports_cancellation(
        self, big_compressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that a cancelled job comes back flagged, not raising and not an error."""
        output = temp_dir / "job_cancelled.comp"
        token = CancelToken()
        job = CompressionJob.from_file(src=big_compressible_file, dest=output)

        result = job.run(progress=lambda f, done, total: token.cancel(), cancel=token)

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None
        assert not output.exists()

    def test_decompression_job_reports_progress(
        self, big_incompressible_file: Path, temp_dir: Path
    ) -> None:
        """Test that DecompressionJob.run reports too, over the compressed size."""
        compressed = temp_dir / "job.comp"
        CompressionJob.from_file(src=big_incompressible_file, dest=compressed).run()

        calls: list[tuple[float, int, int]] = []
        job = DecompressionJob.from_file(src=compressed, dest=temp_dir / "job.out")
        result = job.run(progress=lambda f, done, total: calls.append((f, done, total)))

        assert result.ok is True
        assert len(calls) > 1
        _, done, total = calls[-1]
        assert done == total

    def test_job_result_defaults_to_not_cancelled(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a job that ran to completion is not reported as cancelled."""
        job = CompressionJob.from_file(
            src=sample_text_file, dest=temp_dir / "plain.comp"
        )
        assert job.run().cancelled is False

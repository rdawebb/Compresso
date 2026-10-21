"""Tests for progress reporting and cancellation (the CoreContext layer)."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from compresso import _core
from compresso._core import (
    Cancelled,
    CancelToken,
    compress_file,
    create_archive,
    decompress_file,
    extract_archive,
)
from compresso.frontend._job import to_core_progress

from .helpers import (
    ENTRY_COUNT,
    ENTRY_SIZE,
    JOBS,
    PROGRESS_FILE_SIZE,
    JobFactory,
    Recorder,
)


class Operation(NamedTuple):
    """A job that reads the `source` fixture and writes into an empty directory.

    `run(source, out, **kwargs)` passes `progress` and `cancel` through.
    """

    source: str
    run: Callable[..., Any]


def compress_comp(src: Path, out: Path, **kw: Any) -> None:
    """Compress `src` to a .comp with zstd."""
    compress_file(src, out / "out.comp", "zstd", "balanced", 3, **kw)


def compress_gz(src: Path, out: Path, **kw: Any) -> None:
    """Compress `src` to a standalone .gz."""
    _core.compress_standalone(src, out / "out.gz", "gzip", 1, **kw)


def archive_tar(src: Path, out: Path, **kw: Any) -> None:
    """Archive `src` as a plain tar."""
    create_archive(out / "out.tar", "tar", [src], **kw)


def archive_tar_zst(src: Path, out: Path, **kw: Any) -> None:
    """Archive `src` as a zstd-compressed tar."""
    create_archive(out / "out.tar.zst", "tar.zst", [src], **kw)


def archive_zip(src: Path, out: Path, **kw: Any) -> None:
    """Archive `src` as a zip.

    The only progress comes from libzip's own callback during zip_close.
    """
    create_archive(out / "out.zip", "zip", [src], **kw)


def extract_tar(src: Path, out: Path, **kw: Any) -> None:
    """Extract the tar at `src` into `out`."""
    extract_archive(src, out, [], **kw)


OPERATIONS = {
    "comp": Operation("big_compressible_file", compress_comp),
    "gz": Operation("big_compressible_file", compress_gz),
    "tar": Operation("source_tree", archive_tar),
    "tar.zst": Operation("source_tree", archive_tar_zst),
    "zip": Operation("source_tree", archive_zip),
    "extract-tar": Operation("source_tar", extract_tar),
}


def leftovers(out: Path) -> list[Path]:
    """The files a job left in `out`, temporaries included.

    Args:
        out: The directory the job wrote into.

    Returns:
        Every file under `out`.
    """
    return [p for p in out.rglob("*") if p.is_file()]


@pytest.fixture(scope="module")
def dirs_only_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tree with no file data, so archiving it never enters a data loop.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        The path to the tree's root.
    """
    root = tmp_path_factory.mktemp("progress") / "dirs"
    (root / "a" / "b").mkdir(parents=True)

    return root


@pytest.fixture(params=list(OPERATIONS.values()), ids=list(OPERATIONS))
def op(request: pytest.FixtureRequest) -> Operation:
    """Each row of `OPERATIONS`.

    Args:
        request: Pytest request fixture.

    Returns:
        The operation.
    """
    return request.param


class TestProgress:
    """Test the progress reports themselves."""

    def test_progress_is_monotonic_and_completes(
        self, op: Operation, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that progress arrives during the job, rising to the total.

        The fresh token also shows an uncancelled one doesn't interfere.
        """
        recorder = Recorder()
        op.run(
            request.getfixturevalue(op.source),
            tmp_path,
            progress=recorder,
            cancel=CancelToken(),
        )

        recorder.assert_progressed()
        recorder.assert_monotonic()
        recorder.assert_finished()

    @pytest.mark.parametrize(
        ("name", "total"),
        [
            ("comp", PROGRESS_FILE_SIZE),
            ("tar", ENTRY_COUNT * ENTRY_SIZE),
            ("extract-tar", ENTRY_COUNT * ENTRY_SIZE),
        ],
    )
    def test_total_is_the_input_size(
        self, request: pytest.FixtureRequest, tmp_path: Path, name: str, total: int
    ) -> None:
        """Test that the total is known up front, and every report carries it.

        Plain tar reads each input once, and extraction's validation pass,
        which walks the archive before the pass that writes, reports nothing.
        """
        op = OPERATIONS[name]
        recorder = Recorder()
        op.run(request.getfixturevalue(op.source), tmp_path, progress=recorder)

        recorder.assert_total(total)

    # One backend and one standalone format: the driver loop is shared, and
    # test_codec.c covers every backend
    @pytest.mark.parametrize(
        ("compress", "decompress"),
        [
            pytest.param(
                lambda src, dst: compress_file(src, dst, "zstd", "balanced", 1),
                lambda src, dst, **kw: decompress_file(src, dst, "zstd", **kw),
                id="comp",
            ),
            pytest.param(
                lambda src, dst: _core.compress_standalone(src, dst, "gzip", 1),
                lambda src, dst, **kw: _core.decompress_standalone(
                    src, dst, "gzip", **kw
                ),
                id="gz",
            ),
        ],
    )
    def test_decompression_reports_progress(
        self,
        big_incompressible_file: Path,
        tmp_path: Path,
        compress: Callable[..., object],
        decompress: Callable[..., object],
    ) -> None:
        """Test that decompression reports too, counting compressed bytes consumed."""
        compressed, restored = tmp_path / "in", tmp_path / "out"
        compress(big_incompressible_file, compressed)

        recorder = Recorder()
        decompress(compressed, restored, progress=recorder)

        recorder.assert_progressed()
        recorder.assert_monotonic()
        recorder.assert_finished()
        assert restored.read_bytes() == big_incompressible_file.read_bytes()

    def test_compressed_tar_is_never_on_disk_uncompressed(
        self, source_tree: Path, tmp_path: Path
    ) -> None:
        """Test that tar.zst is compressed as it is written."""
        temps: list[int] = []

        def count_temps(done: int, total: int) -> None:
            """Record how many temps exist at this report."""
            temps.append(len(list(tmp_path.glob(".compresso-*"))))

        create_archive(
            tmp_path / "out.tar.zst", "tar.zst", [source_tree], progress=count_temps
        )

        assert temps
        assert max(temps) == 1


class TestCancellation:
    """Test stopping a running job through a CancelToken."""

    def test_cancel_token(self) -> None:
        """Test that a token starts uncancelled, and cancelling it sticks."""
        token = CancelToken()
        assert token.cancelled is False

        token.cancel()
        token.cancel()
        assert token.cancelled is True

    def test_cancel_leaves_no_output(
        self, op: Operation, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that a cancel mid-run removes the half-written output."""
        token = CancelToken()

        with pytest.raises(Cancelled):
            op.run(
                request.getfixturevalue(op.source),
                tmp_path,
                progress=lambda done, total: token.cancel(),
                cancel=token,
            )

        assert not leftovers(tmp_path)

    def test_token_cancelled_before_the_call(
        self, op: Operation, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that a token cancelled up front stops the job immediately.

        No progress callback is passed, so cancelling doesn't depend on
        progress being requested; archive rows walk a tree of directories
        alone, so the cancel must land during the walk, not in a data loop.
        """
        source = "dirs_only_tree" if op.source == "source_tree" else op.source
        token = CancelToken()
        token.cancel()

        with pytest.raises(Cancelled):
            op.run(request.getfixturevalue(source), tmp_path, cancel=token)

        assert not leftovers(tmp_path)

    @pytest.mark.parametrize("name", ["comp", "tar.zst"])
    def test_cancel_from_another_thread(
        self, request: pytest.FixtureRequest, tmp_path: Path, name: str
    ) -> None:
        """Test that a token can be cancelled by a thread other than the one working.

        The callback waits for the other thread, so the cancel always lands
        before the job could finish.
        """
        op = OPERATIONS[name]
        token = CancelToken()
        started = threading.Event()

        def watcher() -> None:
            """Cancel the token once the job has started."""
            started.wait(timeout=10)
            token.cancel()

        def on_progress(done: int, total: int) -> None:
            """Let the watcher go, and wait for it to cancel."""
            started.set()
            thread.join(timeout=10)

        thread = threading.Thread(target=watcher)
        thread.start()
        try:
            with pytest.raises(Cancelled):
                op.run(
                    request.getfixturevalue(op.source),
                    tmp_path,
                    progress=on_progress,
                    cancel=token,
                )
        finally:
            started.set()
            thread.join(timeout=10)

        assert not leftovers(tmp_path)

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
        create_archive(archive, fmt, [source_tree])

        # Stop midway through the second entry, so at least one has completed
        # and one is genuinely in flight
        token = CancelToken()

        def cancel_partway(done: int, total: int) -> None:
            """Cancel on the first report inside the second entry."""
            if done > ENTRY_SIZE:
                token.cancel()

        with pytest.raises(Cancelled):
            extract_archive(archive, dest, [], progress=cancel_partway, cancel=token)

        extracted = sorted((dest / source_tree.name).iterdir())
        assert extracted, "expected the completed entries to be kept"
        assert len(extracted) < ENTRY_COUNT, "expected the job to have stopped early"

        originals = {p.name: p.read_bytes() for p in source_tree.iterdir()}
        for restored in extracted:
            assert restored.read_bytes() == originals[restored.name], (
                f"{restored.name} was left truncated"
            )

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
                output, fmt, [source_tree], progress=cancel_at_end, cancel=token
            )

        assert not output.exists()


class TestCallbackErrors:
    """Test progress callback error behaviour."""

    def test_exception_from_callback_propagates(
        self, op: Operation, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that the callback's own exception wins, and cleans up like any failure."""

        class Boom(Exception):
            """Mock exception."""

        def explode(done: int, total: int) -> None:
            """Raises Boom to simulate a failed callback."""
            raise Boom("callback failed")

        with pytest.raises(Boom, match="callback failed"):
            op.run(request.getfixturevalue(op.source), tmp_path, progress=explode)

        assert not leftovers(tmp_path)

    def test_non_callable_progress_is_rejected(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that a bad progress argument fails fast rather than mid-compression."""
        with pytest.raises(TypeError, match="progress must be callable"):
            compress_file(
                str(sample_text_file),
                str(tmp_path / "x.comp"),
                "zstd",
                "balanced",
                3,
                progress=42,  # ty: ignore[invalid-argument-type] - the point of the test
            )

    def test_wrong_cancel_type_is_rejected(
        self, sample_text_file: Path, tmp_path: Path
    ) -> None:
        """Test that cancel= only accepts a CancelToken."""
        with pytest.raises(TypeError, match="cancel must be a CancelToken"):
            compress_file(
                str(sample_text_file),
                str(tmp_path / "x.comp"),
                "zstd",
                "balanced",
                3,
                cancel="please stop",  # ty: ignore[invalid-argument-type] - the point of the test
            )


class TestSignalHandling:
    """Test that pending signals are run mid-compression, not just when the call returns."""

    def test_signal_raised_during_callback_aborts(
        self, big_compressible_file: Path, tmp_path: Path
    ) -> None:
        """Test that a signal pending when the bridge runs interrupts the compression."""
        output = tmp_path / "interrupted.comp"

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
        self, big_incompressible_file: Path, tmp_path: Path
    ) -> None:
        """Test that signals are checked even when no progress was requested."""
        output = tmp_path / "alarmed.comp"

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


@pytest.mark.parametrize("job", JOBS.values(), ids=list(JOBS))
class TestJobIntegration:
    """Test that the frontend jobs surface progress and cancellation without raising."""

    def test_reports_progress(
        self, job: JobFactory, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that the job drives the callback with real byte counts."""
        recorder = Recorder()
        result = job.make(request.getfixturevalue(job.source), tmp_path).run(
            progress=recorder
        )

        assert result.ok is True
        assert result.cancelled is False
        recorder.assert_progressed()
        recorder.assert_monotonic()
        recorder.assert_finished()

    def test_reports_cancellation(
        self, job: JobFactory, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """Test that a cancelled job comes back flagged, not raising and not an error."""
        token = CancelToken()
        result = job.make(request.getfixturevalue(job.source), tmp_path).run(
            progress=lambda f, done, total: token.cancel(), cancel=token
        )

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None
        assert not leftovers(tmp_path)


def test_core_progress_computes_fraction() -> None:
    """Test that the job adapter derives the fraction, falling back to the job total."""
    calls: list[tuple[float, int, int]] = []
    on_progress = to_core_progress(lambda *args: calls.append(args), total=8)
    assert on_progress is not None
    on_progress(4, 0)
    on_progress(10, 10)
    assert calls == [(0.5, 4, 8), (1.0, 10, 10)]

    calls.clear()
    on_progress = to_core_progress(lambda *args: calls.append(args), total=0)
    assert on_progress is not None
    on_progress(0, 0)
    assert calls == [(0.0, 0, 0)]

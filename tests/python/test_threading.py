"""Tests for background and async job execution.

`run_async` is exercised through `asyncio.run` inside ordinary sync tests.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from compresso._core import CancelToken
from compresso.frontend._job import JobHandle, JobResult
from compresso.frontend.api import CompressionJob, CompressionOptions
from compresso.frontend.archive_api import ArchiveJob, ArchiveOptions, ExtractJob

# Big enough that a job is still running when the test looks at it
JOB_FILE_SIZE = 12 * 1024 * 1024


@pytest.fixture
def big_file(temp_dir: Path) -> Path:
    """An incompressible file large enough to take a measurable moment.

    Args:
        temp_dir: The temporary directory to use for the file.

    Returns:
        The path to the created file.
    """
    path = temp_dir / "payload.bin"
    path.write_bytes(os.urandom(JOB_FILE_SIZE))
    return path


def slow_job(src: Path, dest: Path) -> CompressionJob:
    """A job slow enough to still be running just after `start()`.

    bzip2 at level 9 on incompressible data is the most CPU-bound path
    available, and releases the GIL around its chunk loop.

    Args:
        src: The source file path.
        dest: The destination file path.

    Returns:
        The compression job.
    """
    return CompressionJob.from_file(
        src=src, dest=dest, options=CompressionOptions(algo="bzip2", level=9)
    )


class TestStart:
    """Tests `start()` and the handle it returns."""

    def test_start_returns_a_handle(self, big_file: Path, temp_dir: Path) -> None:
        """Test that a started job hands back a JobHandle."""
        with slow_job(big_file, temp_dir / "out.comp").start() as handle:
            assert isinstance(handle, JobHandle)
            handle.result(timeout=120)

    def test_result_waits_and_returns_the_outcome(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that `result()` blocks until the job finishes, then reports success."""
        output = temp_dir / "out.comp"

        with slow_job(big_file, output).start() as handle:
            result = handle.result(timeout=120)

        assert isinstance(result, JobResult)
        assert result.ok is True
        assert result.cancelled is False
        assert output.stat().st_size > 0

    def test_done_is_false_until_it_finishes(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that `done()` tracks the job rather than the call to `start()`."""
        with slow_job(big_file, temp_dir / "out.comp").start() as handle:
            assert handle.done() is False

            handle.result(timeout=120)
            assert handle.done() is True

    def test_result_is_repeatable(self, big_file: Path, temp_dir: Path) -> None:
        """Test that asking twice gives the same answer rather than blocking again."""
        with slow_job(big_file, temp_dir / "out.comp").start() as handle:
            first = handle.result(timeout=120)
            second = handle.result(timeout=120)

        assert first is second

    def test_result_times_out_while_running(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that a timeout that expires raises rather than returning a half-result."""
        with (
            slow_job(big_file, temp_dir / "out.comp").start() as handle,
            pytest.raises(TimeoutError),
        ):
            handle.result(timeout=0.001)

    def test_progress_callback_runs_on_the_job_thread(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that the progress callback runs on the job thread."""
        caller = threading.get_ident()
        seen: list[int] = []

        with slow_job(big_file, temp_dir / "out.comp").start(
            progress=lambda f, done, total: seen.append(threading.get_ident())
        ) as handle:
            handle.result(timeout=120)

        assert seen, "expected progress to be reported"
        assert all(ident != caller for ident in seen)
        assert len(set(seen)) == 1, "expected a single job thread"

    def test_start_works_for_archive_jobs(self, big_file: Path, temp_dir: Path) -> None:
        """Test that Threading is shared by every job type, not only compression."""
        output = temp_dir / "out.tar.zst"

        with ArchiveJob.from_paths(sources=[big_file], output=output).start() as handle:
            result = handle.result(timeout=120)

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_start_works_for_extract_jobs(self, big_file: Path, temp_dir: Path) -> None:
        """Test that extraction can be backgrounded too."""
        # Archived as a directory so entries are stored relative to it; a
        # single file given by absolute path is stored with that path
        tree = temp_dir / "tree"
        tree.mkdir()
        payload = tree / big_file.name
        payload.write_bytes(big_file.read_bytes())

        archive = temp_dir / "in.tar"
        ArchiveJob.from_paths(
            sources=[tree], output=archive, options=ArchiveOptions(format="tar")
        ).run()

        dest = temp_dir / "out"
        with ExtractJob.from_archive(
            archive=archive, output_dir=dest
        ).start() as handle:
            result = handle.result(timeout=120)

        assert result.ok is True
        assert (dest / "tree" / payload.name).read_bytes() == payload.read_bytes()


class TestHandleCancellation:
    """Tests stopping a backgrounded job."""

    def test_cancel_stops_the_job(self, big_file: Path, temp_dir: Path) -> None:
        """Test that a cancelled job reports through the flag, not as a failure."""
        output = temp_dir / "cancelled.comp"

        with slow_job(big_file, output).start() as handle:
            handle.cancel()
            result = handle.result(timeout=120)

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None
        assert not output.exists()

    def test_cancel_after_completion_is_harmless(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that canceling after completion is harmless."""
        with slow_job(big_file, temp_dir / "out.comp").start() as handle:
            result = handle.result(timeout=120)

            handle.cancel()

            assert result.ok is True
            assert handle.result(timeout=1) is result

    def test_cancel_is_idempotent(self, big_file: Path, temp_dir: Path) -> None:
        """Test that cancelling twice behaves like cancelling once."""
        with slow_job(big_file, temp_dir / "out.comp").start() as handle:
            handle.cancel()
            handle.cancel()

            result = handle.result(timeout=120)

        assert result.cancelled is True

    def test_supplied_token_cancels_several_jobs_at_once(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that one token passed to several jobs stops all of them."""
        token = CancelToken()

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    slow_job(big_file, temp_dir / f"multi{i}.comp").start(cancel=token)
                )
                for i in range(3)
            ]

            token.cancel()

            # Collected inside the block, so it is the token that stopped
            # them rather than the handles' own cancel on exit
            results = [handle.result(timeout=120) for handle in handles]

        for result in results:
            assert result.cancelled is True


class TestHandleContextManager:
    """Test `with job.start() as handle:`."""

    def test_exit_cancels_the_job(self, big_file: Path, temp_dir: Path) -> None:
        """Test that leaving the block stops a job that is still running."""
        output = temp_dir / "ctx.comp"

        with slow_job(big_file, output).start() as handle:
            pass

        assert handle.done() is True
        assert handle.result(timeout=1).cancelled is True
        assert not output.exists()

    def test_exit_waits_for_the_thread(self, big_file: Path, temp_dir: Path) -> None:
        """Test that the job is not still touching the filesystem after the block."""
        with slow_job(big_file, temp_dir / "ctx.comp").start() as handle:
            pass

        assert handle.done() is True

    def test_a_completed_job_survives_the_block(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that cancelling after the work is done does not undo it."""
        output = temp_dir / "ctx.comp"

        with slow_job(big_file, output).start() as handle:
            result = handle.result(timeout=120)

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_exception_in_the_block_still_cancels(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that an error in the body must not leak a running job."""
        handle: JobHandle | None = None

        with (
            pytest.raises(RuntimeError, match="boom"),
            slow_job(big_file, temp_dir / "ctx.comp").start() as h,
        ):
            handle = h
            raise RuntimeError("boom")

        assert handle is not None
        assert handle.done() is True


class TestRunAsync:
    """Test `run_async`, the asyncio veneer over the same threading path."""

    def test_awaits_to_a_result(self, big_file: Path, temp_dir: Path) -> None:
        """Test that awaiting returns the same JobResult a sync run would."""
        output = temp_dir / "async.comp"

        async def main() -> JobResult:
            return await slow_job(big_file, output).run_async()

        result = asyncio.run(main())

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_does_not_block_the_event_loop(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that other coroutines keep running while the job does."""
        ticks = 0

        async def ticker() -> None:
            """A simple ticker that increments a counter while sleeping."""
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        async def main() -> JobResult:
            tick_task = asyncio.create_task(ticker())
            try:
                return await slow_job(big_file, temp_dir / "async.comp").run_async()

            finally:
                tick_task.cancel()

        result = asyncio.run(main())

        assert result.ok is True
        assert ticks > 1, "the event loop was blocked while the job ran"

    def test_cancelling_the_task_cancels_the_job(
        self, big_file: Path, temp_dir: Path
    ) -> None:
        """Test that cancelling the awaiting task stops the job and cleans up."""
        output = temp_dir / "async_cancelled.comp"
        token = CancelToken()

        async def main() -> None:
            task = asyncio.create_task(
                slow_job(big_file, output).run_async(cancel=token)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(main())

        # asyncio.run joins the executor's worker thread before returning, so
        # the job has already stopped and removed its partial output
        assert token.cancelled is True
        assert not output.exists()

    def test_reports_progress(self, big_file: Path, temp_dir: Path) -> None:
        """Test that progress reporting works the same way through the async path."""
        calls: list[tuple[float, int, int]] = []

        async def main() -> JobResult:
            return await slow_job(big_file, temp_dir / "async.comp").run_async(
                progress=lambda f, done, total: calls.append((f, done, total))
            )

        assert asyncio.run(main()).ok is True
        assert len(calls) > 1
        assert [done for _, done, _ in calls] == sorted(done for _, done, _ in calls)


class TestParallelism:
    """Tests started jobs run at the same time."""

    def test_two_jobs_overlap_in_time(self, temp_dir: Path) -> None:
        """Test that both jobs are mid-flight at the same moment.

        The C layer releases the GIL around every hot loop, so threads give
        real parallelism here rather than time-slicing.
        """
        sources = []
        for i in range(2):
            path = temp_dir / f"p{i}.bin"
            path.write_bytes(os.urandom(JOB_FILE_SIZE))
            sources.append(path)

        stamps: dict[int, list[float]] = {0: [], 1: []}

        def recorder(index: int):
            """Record the progress of a job by appending timestamps to the list."""
            return lambda f, done, total: stamps[index].append(time.monotonic())

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    slow_job(sources[i], temp_dir / f"p{i}.comp").start(
                        progress=recorder(i)
                    )
                )
                for i in range(2)
            ]
            results = [h.result(timeout=300) for h in handles]

        assert all(r.ok for r in results)
        assert all(stamps[i] for i in (0, 1)), "expected progress from both jobs"

        first_window = (stamps[0][0], stamps[0][-1])
        second_window = (stamps[1][0], stamps[1][-1])
        overlap = min(first_window[1], second_window[1]) - max(
            first_window[0], second_window[0]
        )
        assert overlap > 0, "jobs ran one after the other, not in parallel"

    def test_parallel_jobs_produce_correct_output(self, temp_dir: Path) -> None:
        """Test that Concurrency must not corrupt anything.

        Each call gets its own CoreContext on its own stack, rather than state
        on the shared const backend vtables.
        """
        sources = []
        for i in range(4):
            path = temp_dir / f"c{i}.bin"
            path.write_bytes(os.urandom(2 * 1024 * 1024))
            sources.append(path)

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    CompressionJob.from_file(
                        src=sources[i], dest=temp_dir / f"c{i}.comp"
                    ).start()
                )
                for i in range(4)
            ]
            results = [h.result(timeout=300) for h in handles]

        assert all(r.ok for r in results)

        from compresso.frontend.api import DecompressionJob

        for i in range(4):
            restored = temp_dir / f"r{i}.bin"
            assert (
                DecompressionJob.from_file(src=temp_dir / f"c{i}.comp", dest=restored)
                .run()
                .ok
            )
            assert restored.read_bytes() == sources[i].read_bytes()

    def test_cancelling_one_job_leaves_the_others_alone(self, temp_dir: Path) -> None:
        """Test that each job's token is its own."""
        sources = []
        for i in range(3):
            path = temp_dir / f"i{i}.bin"
            path.write_bytes(os.urandom(JOB_FILE_SIZE))
            sources.append(path)

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    slow_job(sources[i], temp_dir / f"i{i}.comp").start()
                )
                for i in range(3)
            ]
            handles[1].cancel()

            results = [h.result(timeout=300) for h in handles]

        assert results[0].ok is True
        assert results[1].cancelled is True
        assert results[2].ok is True
        assert not (temp_dir / "i1.comp").exists()

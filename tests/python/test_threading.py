"""Tests for background and async job execution.

`run_async` is exercised through `asyncio.run` inside ordinary sync tests.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from compresso._core import CancelToken
from compresso.frontend._job import JobHandle, JobResult, ProgressCallback
from compresso.frontend.api import (
    CompressionJob,
    CompressionOptions,
    DecompressionJob,
)
from compresso.frontend.archive_api import ArchiveJob, ArchiveOptions, ExtractJob

# Past the 1 MiB report floor, so the first progress report lands mid-job
# rather than being the final one
JOB_FILE_SIZE = 3 * 1024 * 1024

# How long a gate holds a job before giving up, so a test fails instead of hanging
HOLD_TIMEOUT = 10.0


class Gate:
    """A progress callback that holds its job until released or cancelled.

    Passed as both `progress` and, through `token`, `cancel`: cancelling the
    job lets it go too, which is what stops a handle's cancel-and-join on
    `__exit__` from deadlocking against a held job.
    """

    def __init__(self) -> None:
        """Start closed, with a fresh token and nothing reached yet."""
        self.token = CancelToken()
        self.reached = threading.Event()
        self.timed_out = False
        self._released = threading.Event()

    def __call__(self, fraction: float, done: int, total: int) -> None:
        """Hold the job thread at this report.

        Args:
            fraction: Progress as a fraction of the total.
            done: The number of bytes processed so far.
            total: The total number of bytes to process.
        """
        self.reached.set()
        deadline = time.monotonic() + HOLD_TIMEOUT
        while not (self._released.wait(0.005) or self.token.cancelled):
            if time.monotonic() > deadline:
                self.timed_out = True
                return

    def wait_until_held(self) -> None:
        """Block until the job has reached the gate."""
        assert self.reached.wait(HOLD_TIMEOUT), "the job never reached the gate"

    def release(self) -> None:
        """Let the job run on to completion."""
        self._released.set()


@pytest.fixture
def gate() -> Iterator[Gate]:
    """A gate that fails the test if it ever had to time out.

    Yields:
        The gate.
    """
    g = Gate()
    yield g
    g.release()
    assert not g.timed_out, "a job sat at the gate until it timed out"


@pytest.fixture(scope="module")
def payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An incompressible file past the report floor.

    Shared by the whole module, so tests must only read it.

    Args:
        tmp_path_factory: Pytest temporary path factory fixture.

    Returns:
        The path to the created file.
    """
    path = tmp_path_factory.mktemp("threading") / "payload.bin"
    path.write_bytes(os.urandom(JOB_FILE_SIZE))
    return path


def quick_job(src: Path, dest: Path) -> CompressionJob:
    """A compression job on the fastest codec setting.

    Anything that needs the job held mid-flight gets that from a `Gate`.

    Args:
        src: The source file path.
        dest: The destination file path.

    Returns:
        The compression job.
    """
    return CompressionJob.from_file(
        src=src, dest=dest, options=CompressionOptions(algo="zstd", level=1)
    )


class TestStart:
    """Tests `start()` and the handle it returns."""

    def test_result_waits_and_returns_the_outcome(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that `result()` blocks until the job finishes, then reports success."""
        output = temp_dir / "out.comp"

        with quick_job(payload, output).start() as handle:
            assert isinstance(handle, JobHandle)
            result = handle.result(timeout=30)

        assert isinstance(result, JobResult)
        assert result.ok is True
        assert result.cancelled is False
        assert output.stat().st_size > 0

    def test_done_is_false_until_it_finishes(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that `done()` tracks the job rather than the call to `start()`."""
        with quick_job(payload, temp_dir / "out.comp").start(
            progress=gate, cancel=gate.token
        ) as handle:
            gate.wait_until_held()
            assert handle.done() is False

            gate.release()
            handle.result(timeout=30)
            assert handle.done() is True

    def test_result_is_repeatable(self, payload: Path, temp_dir: Path) -> None:
        """Test that asking twice gives the same answer rather than blocking again."""
        with quick_job(payload, temp_dir / "out.comp").start() as handle:
            first = handle.result(timeout=30)
            second = handle.result(timeout=1)

        assert first is second

    def test_result_times_out_while_running(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that a timeout that expires raises rather than returning a half-result."""
        with quick_job(payload, temp_dir / "out.comp").start(
            progress=gate, cancel=gate.token
        ) as handle:
            gate.wait_until_held()

            with pytest.raises(TimeoutError):
                handle.result(timeout=0.01)

    def test_progress_callback_runs_on_the_job_thread(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that the progress callback runs on the job thread."""
        caller = threading.get_ident()
        seen: list[int] = []

        with quick_job(payload, temp_dir / "out.comp").start(
            progress=lambda f, done, total: seen.append(threading.get_ident())
        ) as handle:
            handle.result(timeout=30)

        assert seen, "expected progress to be reported"
        assert all(ident != caller for ident in seen)
        assert len(set(seen)) == 1, "expected a single job thread"

    def test_start_works_for_archive_jobs(self, payload: Path, temp_dir: Path) -> None:
        """Test that threading is shared by every job type, not only compression."""
        output = temp_dir / "out.tar.zst"

        with ArchiveJob.from_paths(sources=[payload], output=output).start() as handle:
            result = handle.result(timeout=30)

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_start_works_for_extract_jobs(self, payload: Path, temp_dir: Path) -> None:
        """Test that extraction can be backgrounded too."""
        # Archived as a directory so extraction has a nested path to recreate
        tree = temp_dir / "tree"
        tree.mkdir()
        (tree / payload.name).write_bytes(payload.read_bytes())

        archive = temp_dir / "in.tar"
        assert (
            ArchiveJob.from_paths(
                sources=[tree], output=archive, options=ArchiveOptions(format="tar")
            )
            .run()
            .ok
        )

        dest = temp_dir / "out"
        with ExtractJob.from_archive(
            archive=archive, output_dir=dest
        ).start() as handle:
            result = handle.result(timeout=30)

        assert result.ok is True
        assert (dest / "tree" / payload.name).read_bytes() == payload.read_bytes()


class TestHandleCancellation:
    """Tests stopping a backgrounded job."""

    def test_cancel_stops_the_job(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that a cancelled job reports through the flag, not as a failure."""
        output = temp_dir / "cancelled.comp"

        with quick_job(payload, output).start(
            progress=gate, cancel=gate.token
        ) as handle:
            gate.wait_until_held()
            handle.cancel()
            result = handle.result(timeout=30)

        assert result.cancelled is True
        assert result.ok is False
        assert result.error is None
        assert not output.exists()

    def test_cancel_after_completion_is_harmless(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that cancelling a finished job leaves its result alone."""
        with quick_job(payload, temp_dir / "out.comp").start() as handle:
            result = handle.result(timeout=30)

            handle.cancel()

            assert result.ok is True
            assert handle.result(timeout=1) is result

    def test_cancel_is_idempotent(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that cancelling twice behaves like cancelling once."""
        with quick_job(payload, temp_dir / "out.comp").start(
            progress=gate, cancel=gate.token
        ) as handle:
            gate.wait_until_held()
            handle.cancel()
            handle.cancel()

            result = handle.result(timeout=30)

        assert result.cancelled is True

    def test_supplied_token_cancels_several_jobs_at_once(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that one token passed to several jobs stops all of them."""
        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    quick_job(payload, temp_dir / f"multi{i}.comp").start(
                        progress=gate, cancel=gate.token
                    )
                )
                for i in range(3)
            ]

            gate.token.cancel()

            # Collected inside the block, so it is the token that stopped
            # them rather than the handles' own cancel on exit
            results = [handle.result(timeout=30) for handle in handles]

        for result in results:
            assert result.cancelled is True


class TestHandleContextManager:
    """Test `with job.start() as handle:`."""

    def test_exit_cancels_and_waits_for_the_job(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that leaving the block stops a running job and waits for it."""
        output = temp_dir / "ctx.comp"

        with quick_job(payload, output).start(
            progress=gate, cancel=gate.token
        ) as handle:
            gate.wait_until_held()

        assert handle.done() is True
        assert handle.result(timeout=1).cancelled is True
        assert not output.exists()

    def test_a_completed_job_survives_the_block(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that cancelling after the work is done does not undo it."""
        output = temp_dir / "ctx.comp"

        with quick_job(payload, output).start() as handle:
            result = handle.result(timeout=30)

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_exception_in_the_block_still_cancels(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that an error in the body must not leak a running job."""
        handle: JobHandle | None = None

        with (
            pytest.raises(RuntimeError, match="boom"),
            quick_job(payload, temp_dir / "ctx.comp").start(
                progress=gate, cancel=gate.token
            ) as h,
        ):
            handle = h
            gate.wait_until_held()
            raise RuntimeError("boom")

        assert handle is not None
        assert handle.done() is True
        assert handle.result(timeout=1).cancelled is True


class TestRunAsync:
    """Test `run_async`, the asyncio veneer over the same threading path."""

    def test_awaits_to_a_result(self, payload: Path, temp_dir: Path) -> None:
        """Test that awaiting returns the same JobResult a sync run would."""
        output = temp_dir / "async.comp"

        async def main() -> JobResult:
            return await quick_job(payload, output).run_async()

        result = asyncio.run(main())

        assert result.ok is True
        assert output.stat().st_size > 0

    def test_does_not_block_the_event_loop(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that other coroutines keep running while the job does."""

        async def main() -> JobResult:
            task = asyncio.create_task(
                quick_job(payload, temp_dir / "async.comp").run_async(
                    progress=gate, cancel=gate.token
                )
            )
            while not gate.reached.is_set():
                await asyncio.sleep(0.005)

            gate.release()
            return await task

        assert asyncio.run(main()).ok is True

    def test_cancelling_the_task_cancels_the_job(
        self, payload: Path, temp_dir: Path, gate: Gate
    ) -> None:
        """Test that cancelling the awaiting task stops the job and cleans up."""
        output = temp_dir / "async_cancelled.comp"

        async def main() -> None:
            task = asyncio.create_task(
                quick_job(payload, output).run_async(progress=gate, cancel=gate.token)
            )
            while not gate.reached.is_set():
                await asyncio.sleep(0.005)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(main())

        # asyncio.run joins the executor's worker thread before returning, so
        # the job has already stopped and removed its partial output
        assert gate.token.cancelled is True
        assert not output.exists()

    def test_reports_progress(self, payload: Path, temp_dir: Path) -> None:
        """Test that progress reporting works the same way through the async path."""
        calls: list[tuple[float, int, int]] = []

        async def main() -> JobResult:
            return await quick_job(payload, temp_dir / "async.comp").run_async(
                progress=lambda f, done, total: calls.append((f, done, total))
            )

        assert asyncio.run(main()).ok is True
        assert len(calls) > 1
        assert [done for _, done, _ in calls] == sorted(done for _, done, _ in calls)


class TestParallelism:
    """Tests started jobs run at the same time."""

    def test_two_jobs_overlap_in_time(self, payload: Path, temp_dir: Path) -> None:
        """Test that both jobs are mid-flight at the same moment."""
        barrier = threading.Barrier(2, timeout=HOLD_TIMEOUT)

        def meet_once() -> ProgressCallback:
            met = False

            def on_progress(fraction: float, done: int, total: int) -> None:
                nonlocal met
                if not met:
                    met = True
                    barrier.wait()

            return on_progress

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    quick_job(payload, temp_dir / f"p{i}.comp").start(
                        progress=meet_once()
                    )
                )
                for i in range(2)
            ]
            results = [h.result(timeout=30) for h in handles]

        assert all(r.ok for r in results), [r.error for r in results]

    def test_parallel_jobs_produce_correct_output(self, temp_dir: Path) -> None:
        """Test that concurrency must not corrupt anything.

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
            results = [h.result(timeout=60) for h in handles]

        assert all(r.ok for r in results)

        for i in range(4):
            restored = temp_dir / f"r{i}.bin"
            assert (
                DecompressionJob.from_file(src=temp_dir / f"c{i}.comp", dest=restored)
                .run()
                .ok
            )
            assert restored.read_bytes() == sources[i].read_bytes()

    def test_cancelling_one_job_leaves_the_others_alone(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that each job's token is its own."""
        gates = [Gate() for _ in range(3)]

        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    quick_job(payload, temp_dir / f"i{i}.comp").start(
                        progress=gates[i], cancel=gates[i].token
                    )
                )
                for i in range(3)
            ]
            for g in gates:
                g.wait_until_held()

            handles[1].cancel()
            gates[0].release()
            gates[2].release()

            results = [h.result(timeout=30) for h in handles]

        assert results[0].ok is True
        assert results[1].cancelled is True
        assert results[2].ok is True
        assert not (temp_dir / "i1.comp").exists()
        assert not any(g.timed_out for g in gates)

    def test_zip_creation_lets_other_threads_run(
        self, payload: Path, temp_dir: Path
    ) -> None:
        """Test that libzip compresses inside zip_close without holding the GIL."""
        job = ArchiveJob.from_paths(
            sources=[payload],
            output=temp_dir / "out.zip",
            options=ArchiveOptions(format="zip"),
        )

        ticks = 0
        with job.start() as handle:
            while not handle.done():
                time.sleep(0.001)
                ticks += 1
            assert handle.result(timeout=30).ok is True

        assert ticks >= 10, ticks

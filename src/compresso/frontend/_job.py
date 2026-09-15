"""Shared job-lifecycle primitives for the frontend APIs."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, Self, TypeVar, runtime_checkable

from .._core import CancelToken

# Progress callback signature: (fraction, done_bytes, total_bytes).
ProgressCallback = Callable[[float, int, int], None]

# The plan a job executes; each job narrows it to its own plan type
PlanT = TypeVar("PlanT")


@dataclass
class JobResult:
    """Holds the result of a compression, decompression, or archive job.

    Attributes:
        ok: Indicates if the job was successful.
        error: The error encountered, if any.
        plan: The associated plan (a compression, decompression, archive, or
            extraction plan).
        cancelled: True if the job stopped because its cancel token was set.
    """

    ok: bool
    error: BaseException | None
    plan: object
    cancelled: bool = False


def to_core_progress(
    progress: ProgressCallback | None, total: int
) -> Callable[[int, int], None] | None:
    """Adapt a :data:`ProgressCallback` to the two-argument form `_core` calls.

    Args:
        progress: The progress callback function.
        total: The total size of the input, used as a fallback when the C layer
            does not report progress.

    Returns:
        A function that can be used as a progress callback for `_core` calls,
        or None if no callback was provided.
    """
    if progress is None:
        return None

    def on_progress(done: int, core_total: int) -> None:
        effective: int = core_total or total
        fraction: float = (done / effective) if effective else 0.0
        progress(fraction, done, effective)

    return on_progress


@runtime_checkable
class Job(Protocol):
    """Structural contract shared by every frontend job.

    A job exposes the ``plan`` it will execute and a ``run`` method that
    performs the work and returns a :class:`JobResult` (never raising).
    """

    plan: object

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Execute the job and return its result."""
        ...


class JobHandle:
    """A job running on a background thread.

    Returned by `ThreadedJob.start`; the work itself happens in the C extension
    with the GIL released, so a started job runs in parallel with the calling thread.

    Used as a context manager, the job is cancelled and waited for on the way out.
    """

    def __init__(self, token: CancelToken) -> None:
        """Initialise a handle around a not-yet-started job.

        Args:
            token: The cancel token handed to the job being run.
        """
        self._token: CancelToken = token
        self._finished = threading.Event()
        self._result: JobResult | None = None
        self._thread: threading.Thread | None = None

    def _start(
        self, job: ThreadedJob[Any], progress: ProgressCallback | None, name: str
    ) -> None:
        """Run `job` on a new daemon thread.

        Args:
            job: The job to execute.
            progress: Optional progress callback, invoked on the job thread.
            name: Thread name, to make the worker identifiable in a traceback.
        """

        def target() -> None:
            try:
                self._result = job.run(progress, self._token)

            except BaseException as e:  # noqa: BLE001
                # `run` never raises, but a progress callback raising outside
                # Exception would; recording it stops result() blocking forever
                self._result = JobResult(ok=False, error=e, plan=job.plan)

            finally:
                self._finished.set()

        # Daemon: a stuck job must not hold the interpreter open, and cancel()
        # is the clean way to stop one
        self._thread = threading.Thread(target=target, name=name, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Ask the job to stop.

        Safe to call from any thread, more than once, and after the job has
        already finished. Returns immediately: the job stops within one 64 KB
        chunk, leaving no partial destination file.
        """
        self._token.cancel()

    def done(self) -> bool:
        """Return whether the job has finished, successfully or not."""
        return self._finished.is_set()

    def result(self, timeout: float | None = None) -> JobResult:
        """Wait for the job to finish and return its result.

        Args:
            timeout: Seconds to wait; None waits indefinitely.

        Returns:
            JobResult: The outcome, including a cancelled job's result.

        Raises:
            TimeoutError: If the job is still running when `timeout` expires.
        """
        if not self._finished.wait(timeout):
            raise TimeoutError("Job did not finish within the timeout")

        # _finished is only ever set after _result is assigned
        assert self._result is not None
        return self._result

    def __enter__(self) -> Self:
        """Return this handle for use in a `with` block."""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Cancel the job and wait for it to stop.

        Waits as well as cancels, so the job is no longer touching the
        filesystem once the block is left.
        """
        self.cancel()
        if self._thread is not None:
            self._thread.join()


class ThreadedJob(Generic[PlanT]):
    """Adds background and async execution to a job.

    Concrete jobs supply a blocking `run` that reports failure through
    `JobResult` rather than raising; `start` and `run_async` build on it.

    The progress callback runs on the job's thread. A GUI or anything else
    with thread affinity must marshal it back itself.
    """

    plan: PlanT

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Execute the job synchronously; provided by each concrete job."""
        raise NotImplementedError

    def start(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobHandle:
        """Run the job on a background thread.

        Args:
            progress: Optional progress callback, invoked on the job thread.
            cancel: Optional token to cancel with. One is created if omitted;
                passing your own lets a single token stop several jobs at once.

        Returns:
            JobHandle: A handle to wait on, poll, or cancel.
        """
        handle = JobHandle(cancel if cancel is not None else CancelToken())
        handle._start(self, progress, name=f"compresso-{type(self).__name__}")
        return handle

    async def run_async(
        self,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> JobResult:
        """Run the job on a worker thread and await its result.

        Cancelling the awaiting task cancels the job.

        Args:
            progress: Optional progress callback, invoked on the worker thread.
            cancel: Optional token to cancel with; one is created if omitted.

        Returns:
            JobResult: The outcome of the job.
        """
        token: CancelToken = cancel if cancel is not None else CancelToken()
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, self.run, progress, token)

        try:
            return await future

        except asyncio.CancelledError:
            # The executor thread runs on until it notices the token; awaiting
            # it is what the caller drops, so the token is what stops the work
            token.cancel()
            raise

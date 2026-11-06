"""The `test` command, checking the integrity of archives and compressed files."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Annotated

from .._core import TrailingDataWarning
from ..frontend.api import VerificationJob
from ._app import app
from ._render import handles_errors, succeed
from ._run import InputFailed, require_files, run_all, run_job


def _verify_one(job: VerificationJob, quiet: bool) -> None:
    """Check one input.

    Args:
        job: The planned job.
        quiet: Whether to suppress all output.
    """
    plan = job.plan

    if not plan.can_run:
        raise InputFailed(f"{plan.src}: {plan.reason_if_unavailable}")

    size: int = plan.src.stat().st_size
    run_job(job, plan.src, "Testing", size, quiet=quiet, what="Test")

    if not quiet:
        succeed(f"OK: {plan.src}")


@handles_errors("Test")
def verify(
    inputs: Annotated[
        list[Path],
        app.Argument(help="Archives or compressed files", readable=False),
    ],
    threads: Annotated[
        int,
        app.Option(
            "--threads",
            "-T",
            min=0,
            help="Worker threads for xz, or 0 for one per core",
        ),
    ] = 0,
    quiet: Annotated[
        bool, app.Option("--quiet", "-q", help="Suppress all output")
    ] = False,
) -> None:
    """Check archives and compressed files for damage, writing nothing.
    \f

    Each input is decoded in full, as `extract` would, so this catches whatever
    its format can detect: a checksum or length that doesn't match, or a file
    cut short. Trailing data fails too, as `gzip -t` treats it, though `extract`
    only warns. A plain `.tar` has no checksum over its file data, so only its
    headers are checked, and that the data they declare is there.

    Args:
        inputs: The archives or compressed files to check.
        threads: Worker threads for xz (default: one per core).
        quiet: If True, suppress all output.
    """
    require_files(inputs)

    jobs = (VerificationJob.from_file(src=path, threads=threads) for path in inputs)

    with warnings.catch_warnings():
        warnings.simplefilter("error", TrailingDataWarning)
        code = run_all(jobs, lambda job: _verify_one(job, quiet), "Test")

    sys.exit(code)


def register() -> None:
    """Attach this command to the app."""
    app.command(name="test", aliases=["t"])(verify)

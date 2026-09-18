"""Dispatcher for the two smart commands `compress` and `extract`."""

from __future__ import annotations

from pathlib import Path

from ..frontend.api import STANDALONE_FORMATS, canonical_standalone_format

# Formats whose container holds many entries
ARCHIVE_FORMATS: frozenset[str] = frozenset(
    {
        "tar",
        "tar.gz",
        "tgz",
        "tar.bz2",
        "tbz2",
        "tar.xz",
        "txz",
        "tar.zst",
        "tzst",
        "zip",
    }
)

# What `compress` reaches for when given several inputs and no format
DEFAULT_ARCHIVE_FORMAT = "zip"


def infer_format(output: Path | None) -> str | None:
    """Infer a format from an output filename.

    Args:
        output: The path the user asked to write, or None.

    Returns:
        The format named by the suffix, or None if it names none — which
        includes `.comp`, whose container is the default anyway.
    """
    if output is None:
        return None

    name: str = output.name.lower()

    known: list[str] = sorted(
        ARCHIVE_FORMATS | set(STANDALONE_FORMATS), key=len, reverse=True
    )
    for candidate in known:
        if name.endswith(f".{candidate}"):
            return candidate

    return None


def is_archive_format(fmt: str | None) -> bool:
    """Return whether `fmt` names a container that holds many entries.

    Args:
        fmt: The format string to check.

    Returns:
        True if `fmt` names an archive format, False otherwise.
    """
    return fmt is not None and fmt.lower() in ARCHIVE_FORMATS


def default_archive_output(first_input: Path, fmt: str) -> Path:
    """Name an archive the caller did not name.

    Args:
        first_input: The first source path, which the archive is named after.
        fmt: The archive format, used as the suffix.

    Returns:
        A path beside the input, e.g. `src/` in tar.zst becomes `src.tar.zst`.
    """
    # A trailing separator would otherwise make the name empty
    stem: str = first_input.resolve().name
    return first_input.resolve().parent / f"{stem}.{fmt}"


def resolve_single_output(output: Path | None, default: Path) -> Path:
    """Work out where a single-file job should write.

    `-o` means a directory for an archive, but for one file it is the file itself;
    an existing directory receives the file, anything else is the file.

    Args:
        output: The `-o` value, or None.
        default: Where the job would write if not told otherwise.

    Returns:
        The destination path.
    """
    if output is None:
        return default

    if output.is_dir():
        return output / default.name

    return output


def format_is_single_file(fmt: str | None) -> bool:
    """Return whether `fmt` names a container holding exactly one file.

    Args:
        fmt: The format string to check.

    Returns:
        True if `fmt` names a single-file format, False otherwise.
    """
    return canonical_standalone_format(fmt) is not None

"""Format names and the rules for reading one from an output path."""

from __future__ import annotations

from pathlib import Path

from .._core import format_is_archive, standalone_formats

# What an archive is written as when neither the caller nor its name says
DEFAULT_ARCHIVE_FORMAT = "zip"

_STANDALONE = standalone_formats()

# Single-file container formats, as opposed to the Compresso container or an
# archive, including aliases
STANDALONE_FORMATS: dict[str, str] = {
    alias: f["name"] for f in _STANDALONE for alias in (f["name"], *f["aliases"])
}

# Suffix each standalone format conventionally uses, for naming an output the
# caller did not name themselves
STANDALONE_SUFFIXES: dict[str, str] = {
    f["name"]: "." + f["exts"][0] for f in _STANDALONE
}


def canonical_standalone_format(name: str | None) -> str | None:
    """Return the canonical name for a single-file format, or None.

    Args:
        name: A format as the user wrote it, e.g. "gz".

    Returns:
        The canonical name (e.g. "gzip"), or None if it is not a single-file
        format; includes None, archive formats and unknown names.
    """
    if not name:
        return None

    return STANDALONE_FORMATS.get(name.lower())


def infer_format(output: Path | None) -> str | None:
    """Infer a format from an output filename.

    Args:
        output: The path the user asked to write, or None.

    Returns:
        The format named by the suffix, or None if it names none; includes
        `.comp`, whose container is the default anyway.
    """
    if output is None:
        return None

    # Mirrors the core (`x.tar.lz4` is tar in lz4); unwritable formats
    # still match, so the default is never written under their suffix
    suffix: str = output.suffix.lower().lstrip(".")
    if Path(output.stem).suffix.lower() == ".tar" and format_is_archive(
        f"tar.{suffix}"
    ):
        return f"tar.{suffix}"

    if suffix in STANDALONE_FORMATS or format_is_archive(suffix):
        return suffix

    return None

"""Format names and the rules for reading one from an output path."""

from __future__ import annotations

from pathlib import Path

from .._core import archive_capabilities, format_is_archive, standalone_formats

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


def archive_formats() -> dict[str, list[str]]:
    """Return each available archive format with its compressed variants.

    Returns:
        Each archive format (e.g. "tar") mapped to the variants the core
        writes, named by the extension an output is given (e.g. "tar.gz").
    """
    # Asked of the core, which names a variant by any of the codec's names
    return {
        archive["name"]: [
            f"{archive['name']}.{fmt['exts'][0]}"
            for fmt in _STANDALONE
            if format_is_archive(f"{archive['name']}.{fmt['exts'][0]}")
        ]
        for archive in archive_capabilities()
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
        The format named by the suffix, "comp" for the Compresso container,
        or None if it names none.
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

    if suffix == "comp" or suffix in STANDALONE_FORMATS or format_is_archive(suffix):
        return suffix

    return None


def gzip_stored_mtime(path: Path) -> int | None:
    """Return the modification time a gzip header stores.

    Args:
        path: A gzip file.

    Returns:
        Its MTIME, in seconds since the epoch, or None if none is stored.
    """
    with path.open("rb") as f:
        # MTIME, bytes 4-7; zero means none was stored
        return int.from_bytes(f.read(8)[4:], "little") or None

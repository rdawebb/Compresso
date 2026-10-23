"""The `formats` command, showing the algorithms and formats available."""

from __future__ import annotations

from .._core import archive_capabilities, format_is_archive, standalone_formats
from ..introspect.capabilities import list_capabilities
from ._app import app
from ._render import cancelled, fail
from ._render.formats import list_formats


def formats() -> None:
    """List the available compression algorithms and file formats."""
    try:
        caps = list_capabilities()

        if not caps:
            fail("No compression backends available")

        standalone = standalone_formats()
        # Asked of the core, which names a variant by any of the codec's
        # names; listed by the extension an output is given
        archives = {
            archive["name"]: [
                f"{archive['name']}.{fmt['exts'][0]}"
                for fmt in standalone
                if format_is_archive(f"{archive['name']}.{fmt['exts'][0]}")
            ]
            for archive in archive_capabilities()
        }

        list_formats(caps, standalone, archives)

    except KeyboardInterrupt:
        cancelled("Listing")

    except Exception as e:
        fail(f"Error listing formats: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(name="formats")(formats)

"""The `formats` command, showing the algorithms and formats available."""

from __future__ import annotations

from .._core import standalone_formats
from ..frontend._formats import archive_formats
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

        list_formats(caps, standalone_formats(), archive_formats())

    except KeyboardInterrupt:
        cancelled("Format listing")

    except Exception as e:
        fail(f"Error listing formats: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(name="formats")(formats)

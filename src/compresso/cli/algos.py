"""The `list` command, showing the available compression backends."""

from __future__ import annotations

from ..introspect.capabilities import list_capabilities
from ._app import app
from ._render import cancelled, fail


def list_algos() -> None:
    """List all available compression algorithms."""
    try:
        caps = list_capabilities()

        if not caps:
            fail("No compression backends available")

        print(f"Available compression algorithms: {len(caps)}\n")

        for cap in caps:
            app.echo(message=app.style(text=f"- {cap.name}", fg="green", bold=True))
            levels: str = (
                "none" if cap.min_level is None else f"{cap.min_level}-{cap.max_level}"
            )
            app.echo(message=app.style(text=f"  ID:     {cap.id}"))
            app.echo(message=app.style(text=f"  Levels: {levels}\n"))

    except KeyboardInterrupt:
        cancelled("Listing")

    except Exception as e:
        fail(f"Error listing algorithms: {e}")


def register() -> None:
    """Attach this command to the app."""
    app.command(name="list", aliases=["l", "ls"])(list_algos)

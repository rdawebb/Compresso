"""Tests for the package's public surface."""

import compresso
import compresso.frontend


def test_frontend_exports_are_all_public() -> None:
    """Test that the top level re-exports everything the frontend does."""
    missing = set(compresso.frontend.__all__) - set(compresso.__all__)
    assert not missing, f"exported by compresso.frontend only: {sorted(missing)}"


def test_every_export_resolves() -> None:
    """Test that each name in __all__ is actually bound in the package."""
    for name in compresso.__all__:
        assert hasattr(compresso, name), name

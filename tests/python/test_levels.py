"""Tests for the shared compression-level sentinel."""

import pytest

from compresso._levels import LEVEL_AUTO, to_core_level


@pytest.mark.parametrize("level,expected", [(None, LEVEL_AUTO), (0, 0), (9, 9)])
def test_to_core_level(level: int | None, expected: int) -> None:
    """Test that None becomes the core's default and 0 stays a real level."""
    assert to_core_level(level) == expected

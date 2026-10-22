"""Tests for the capabilities module."""

from collections.abc import Callable

import pytest

from compresso.introspect.capabilities import (
    BackendCapabilities,
    get_by_id,
    get_by_name,
    list_capabilities,
)

# The IDs are written into every .comp header, so they must never change
BACKEND_IDS = {"zlib": 1, "bzip2": 2, "lzma": 3, "zstd": 4, "lz4": 5, "snappy": 6}


class TestAcceptsLevel:
    """Test BackendCapabilities.accepts_level."""

    @pytest.mark.parametrize(
        "level,expected", [(None, True), (1, True), (9, True), (0, False), (10, False)]
    )
    def test_ranged_backend(self, level: int | None, expected: bool) -> None:
        """Test that only the default and in-range levels are accepted."""
        cap = BackendCapabilities(name="bzip2", id=2, min_level=1, max_level=9)
        assert cap.accepts_level(level) is expected

    @pytest.mark.parametrize("level,expected", [(None, True), (0, False), (1, False)])
    def test_backend_without_levels(self, level: int | None, expected: bool) -> None:
        """Test that a backend without levels accepts only the default."""
        cap = BackendCapabilities(name="snappy", id=6, min_level=None, max_level=None)
        assert cap.accepts_level(level) is expected


class TestBackendLookup:
    """Test that listing and both lookups agree on every compiled backend."""

    def test_list_holds_exactly_the_compiled_backends(self) -> None:
        """Test the full name-to-ID mapping, which also rules out duplicates."""
        caps = list_capabilities()

        assert {cap.name: cap.id for cap in caps} == BACKEND_IDS
        assert len(caps) == len(BACKEND_IDS)

    @pytest.mark.parametrize("name,algo_id", BACKEND_IDS.items())
    def test_name_and_id_find_the_same_backend(self, name: str, algo_id: int) -> None:
        """Test that both lookups return the one cached object for a backend."""
        by_name = get_by_name(name)

        assert by_name is not None
        assert (by_name.name, by_name.id) == (name, algo_id)
        assert get_by_id(algo_id) is by_name

    @pytest.mark.parametrize(
        ("lookup", "key"),
        [
            (get_by_name, "nonexistent_backend"),
            (get_by_name, "ZLIB"),  # Names are case-sensitive
            (get_by_name, ""),
            (get_by_id, 9999),
            (get_by_id, -1),
            (get_by_id, 0),  # The unused slot
        ],
    )
    def test_unknown_key_is_none(
        self, lookup: Callable[[object], object], key: object
    ) -> None:
        """Test that a name or ID no backend has finds nothing."""
        assert lookup(key) is None

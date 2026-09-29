"""Tests for the capabilities module."""

import pytest

from compresso.introspect.capabilities import (
    BackendCapabilities,
    get_by_id,
    get_by_name,
    list_capabilities,
)

# The IDs are written into every .comp header, so they must never change
BACKEND_IDS = {"zlib": 1, "bzip2": 2, "lzma": 3, "zstd": 4, "lz4": 5, "snappy": 6}


class TestBackendCapabilities:
    """Test the BackendCapabilities dataclass."""

    def test_capabilities_frozen(self) -> None:
        """Test that BackendCapabilities is frozen (immutable)."""
        cap = BackendCapabilities(name="zlib", id=1, min_level=0, max_level=9)

        # Frozen dataclass should not allow assignment
        with pytest.raises((AttributeError, TypeError)):
            cap.name = "bzip2"  # type: ignore


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


class TestListCapabilities:
    """Test the list_capabilities function."""

    def test_list_capabilities_cached(self) -> None:
        """Test that capabilities are cached."""
        caps1 = list_capabilities()
        caps2 = list_capabilities()

        # Should return the same list instance (cached)
        assert caps1 is caps2


class TestGetByName:
    """Test the get_by_name function."""

    def test_get_by_name_invalid(self) -> None:
        """Test getting a non-existent backend."""
        cap = get_by_name("nonexistent_backend")
        assert cap is None

    def test_get_by_name_case_sensitive(self) -> None:
        """Test that backend names are case-sensitive."""
        cap_lower = get_by_name("zlib")
        cap_upper = get_by_name("ZLIB")

        assert cap_lower is not None
        # ZLIB (uppercase) should not exist
        assert cap_upper is None

    def test_get_by_name_empty_string(self) -> None:
        """Test getting backend with empty string."""
        cap = get_by_name("")
        assert cap is None


class TestGetById:
    """Test the get_by_id function."""

    @pytest.mark.parametrize("algo_id", [9999, -1, 0])
    def test_unknown_id_is_none(self, algo_id: int) -> None:
        """Test that an ID no backend has, including the unused slot 0, finds nothing."""
        assert get_by_id(algo_id) is None


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

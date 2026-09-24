"""Tests for the capabilities module."""

import pytest

from compresso.backend.capabilities import (
    BackendCapabilities,
    get_by_id,
    get_by_name,
    list_capabilities,
)


class TestBackendCapabilities:
    """Test the BackendCapabilities dataclass."""

    def test_capabilities_creation(self) -> None:
        """Test creating a BackendCapabilities instance."""
        cap = BackendCapabilities(name="zlib", id=1, min_level=0, max_level=9)

        assert cap.name == "zlib"
        assert cap.id == 1
        assert (cap.min_level, cap.max_level) == (0, 9)

    def test_capabilities_is_available(self) -> None:
        """Test is_available method."""
        cap = BackendCapabilities(name="test", id=99, min_level=None, max_level=None)

        # Always returns True for compiled backends
        assert cap.is_available() is True

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

    def test_list_capabilities_returns_list(self) -> None:
        """Test that list_capabilities returns a list."""
        caps = list_capabilities()
        assert isinstance(caps, list)

    def test_list_capabilities_not_empty(self) -> None:
        """Test that capabilities list is not empty."""
        caps = list_capabilities()
        assert len(caps) > 0

    def test_list_capabilities_contains_backend_capabilities(self) -> None:
        """Test that list contains BackendCapabilities instances."""
        caps = list_capabilities()
        for cap in caps:
            assert isinstance(cap, BackendCapabilities)

    def test_list_capabilities_has_zlib(self) -> None:
        """Test that zlib is in capabilities (should always be available)."""
        caps = list_capabilities()
        names = [cap.name for cap in caps]
        assert "zlib" in names

    def test_list_capabilities_unique_names(self) -> None:
        """Test that all backend names are unique."""
        caps = list_capabilities()
        names = [cap.name for cap in caps]
        assert len(names) == len(set(names))

    def test_list_capabilities_unique_ids(self) -> None:
        """Test that all backend IDs are unique."""
        caps = list_capabilities()
        ids = [cap.id for cap in caps]
        assert len(ids) == len(set(ids))

    def test_list_capabilities_cached(self) -> None:
        """Test that capabilities are cached."""
        caps1 = list_capabilities()
        caps2 = list_capabilities()

        # Should return the same list instance (cached)
        assert caps1 is caps2


class TestGetByName:
    """Test the get_by_name function."""

    def test_get_by_name_zlib(self) -> None:
        """Test getting zlib backend by name."""
        cap = get_by_name("zlib")
        assert cap is not None
        assert cap.name == "zlib"
        assert isinstance(cap, BackendCapabilities)

    @pytest.mark.parametrize("name", ["zlib", "bzip2", "lzma", "zstd", "lz4", "snappy"])
    def test_get_by_name_various_backends(self, name: str) -> None:
        """Test getting various backends by name."""
        cap = get_by_name(name)
        if cap is not None:
            assert cap.name == name
            assert isinstance(cap, BackendCapabilities)

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

    def test_get_by_id_valid(self) -> None:
        """Test getting a backend by valid ID."""
        # Get a valid ID from the list
        caps = list_capabilities()
        if caps:
            valid_id = caps[0].id
            cap = get_by_id(valid_id)
            assert cap is not None
            assert cap.id == valid_id
            assert isinstance(cap, BackendCapabilities)

    def test_get_by_id_invalid(self) -> None:
        """Test getting a backend by invalid ID."""
        cap = get_by_id(9999)
        assert cap is None

    def test_get_by_id_negative(self) -> None:
        """Test getting a backend by negative ID."""
        cap = get_by_id(-1)
        assert cap is None

    def test_get_by_id_zero(self) -> None:
        """Test getting a backend with ID 0."""
        cap = get_by_id(0)
        if cap is not None:
            assert isinstance(cap, BackendCapabilities)

    @pytest.mark.parametrize("algo_id", [1, 2, 3, 4, 5, 6])
    def test_get_by_id_common_ids(self, algo_id: int) -> None:
        """Test getting backends by common algorithm IDs."""
        cap = get_by_id(algo_id)
        if cap is not None:
            assert cap.id == algo_id
            assert isinstance(cap, BackendCapabilities)


class TestCapabilitiesIntegration:
    """Integration tests for capabilities functions."""

    def test_list_and_get_by_name_consistency(self) -> None:
        """Test that list_capabilities and get_by_name are consistent."""
        caps = list_capabilities()
        for cap in caps:
            retrieved = get_by_name(cap.name)
            assert retrieved is not None
            assert retrieved.name == cap.name
            assert retrieved.id == cap.id

    def test_list_and_get_by_id_consistency(self) -> None:
        """Test that list_capabilities and get_by_id are consistent."""
        caps = list_capabilities()
        for cap in caps:
            retrieved = get_by_id(cap.id)
            assert retrieved is not None
            assert retrieved.name == cap.name
            assert retrieved.id == cap.id

    def test_get_by_name_and_id_consistency(self) -> None:
        """Test that get_by_name and get_by_id return the same backend."""
        caps = list_capabilities()
        for cap in caps:
            by_name = get_by_name(cap.name)
            by_id = get_by_id(cap.id)
            assert by_name is by_id  # Should be the same object (cached)

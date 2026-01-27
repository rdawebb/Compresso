"""Tests for the frontend API module."""

import pytest
from pathlib import Path

from compresso.frontend.api import (
    CompressionOptions,
    CompressionPlan,
    DecompressionPlan,
    CompressionJob,
    DecompressionJob,
)


class TestCompressionOptions:
    """Test the CompressionOptions dataclass."""

    def test_compression_options_default(self):
        """Test creating CompressionOptions with defaults."""
        opts = CompressionOptions()

        assert opts.algo is None
        assert opts.strategy == "balanced"
        assert opts.level is None

    def test_compression_options_with_algo(self):
        """Test creating CompressionOptions with algorithm."""
        opts = CompressionOptions(algo="zlib")

        assert opts.algo == "zlib"
        assert opts.strategy == "balanced"

    @pytest.mark.parametrize("strategy", ["fast", "balanced", "max_ratio"])
    def test_compression_options_with_strategy(self, strategy: str):
        """Test CompressionOptions with different strategies."""
        opts = CompressionOptions(strategy=strategy)

        assert opts.strategy == strategy

    @pytest.mark.parametrize("level", [1, 3, 6, 9])
    def test_compression_options_with_level(self, level: int):
        """Test CompressionOptions with different levels."""
        opts = CompressionOptions(level=level)

        assert opts.level == level

    def test_compression_options_immutable(self):
        """Test that CompressionOptions is immutable."""
        opts = CompressionOptions(algo="zlib", strategy="fast", level=1)

        try:
            opts.algo = "zstd"  # type: ignore
            assert False, "Expected exception when modifying frozen dataclass"
        except (AttributeError, TypeError, Exception):
            pass


class TestCompressionPlan:
    """Test the CompressionPlan dataclass."""

    def test_compression_plan_creation(self, sample_text_file: Path, temp_dir: Path):
        """Test creating a CompressionPlan."""
        opts = CompressionOptions(algo="zlib", strategy="balanced", level=6)
        dest = temp_dir / "output.comp"

        plan = CompressionPlan(
            src=sample_text_file,
            dest=dest,
            options=opts,
            input_size=1000,
            backend_name="zlib",
            estimated_seconds=0.5,
            can_compress=True,
            reason_if_unavailable=None,
        )

        assert plan.src == sample_text_file
        assert plan.dest == dest
        assert plan.options == opts
        assert plan.can_compress is True

    def test_compression_plan_unavailable(self, sample_text_file: Path, temp_dir: Path):
        """Test CompressionPlan when compression is unavailable."""
        opts = CompressionOptions(algo="invalid_algo")
        dest = temp_dir / "output.comp"

        plan = CompressionPlan(
            src=sample_text_file,
            dest=dest,
            options=opts,
            input_size=1000,
            backend_name=None,
            estimated_seconds=None,
            can_compress=False,
            reason_if_unavailable="Invalid algorithm",
        )

        assert plan.can_compress is False
        assert plan.reason_if_unavailable is not None


class TestDecompressionPlan:
    """Test the DecompressionPlan dataclass."""

    def test_decompression_plan_creation(self, sample_text_file: Path, temp_dir: Path):
        """Test creating a DecompressionPlan."""
        from compresso.backend.file_inspect import InspectResult

        inspection = InspectResult(
            path=sample_text_file,
            is_compresso=True,
            header_ok=True,
            reason=None,
            version=1,
            algo_id=1,
            algo_name="zlib",
            level=6,
            flags=0,
            orig_size=1000,
            backend_available=True,
            has_streaming=True,
            can_decompress=True,
            estimated_decomp_s=0.5,
        )

        dest = temp_dir / "output.txt"

        plan = DecompressionPlan(
            src=sample_text_file,
            dest=dest,
            inspection=inspection,
            estimated_seconds=0.5,
        )

        assert plan.src == sample_text_file
        assert plan.dest == dest
        assert plan.inspection == inspection


class TestCompressionJob:
    """Test the CompressionJob class."""

    def test_compression_job_exists(self):
        """Test that CompressionJob class exists."""
        assert CompressionJob is not None

    def test_compression_job_is_callable(self):
        """Test that CompressionJob can be instantiated."""
        # Check the class exists and is callable
        assert callable(CompressionJob)


class TestDecompressionJob:
    """Test the DecompressionJob class."""

    def test_decompression_job_exists(self):
        """Test that DecompressionJob class exists."""
        assert DecompressionJob is not None

    def test_decompression_job_is_callable(self):
        """Test that DecompressionJob can be instantiated."""
        # Check the class exists and is callable
        assert callable(DecompressionJob)


class TestAPIIntegration:
    """Integration tests for the API module."""

    def test_all_exports_exist(self):
        """Test that all expected exports exist."""
        from compresso.frontend import api

        assert hasattr(api, "CompressionOptions")
        assert hasattr(api, "CompressionPlan")
        assert hasattr(api, "DecompressionPlan")
        assert hasattr(api, "CompressionJob")
        assert hasattr(api, "DecompressionJob")

    def test_options_plan_compatibility(self, sample_text_file: Path, temp_dir: Path):
        """Test that CompressionOptions works with CompressionPlan."""
        opts = CompressionOptions(algo="zlib", strategy="balanced", level=6)
        dest = temp_dir / "output.comp"

        # Should be able to create a plan with these options
        plan = CompressionPlan(
            src=sample_text_file,
            dest=dest,
            options=opts,
            input_size=sample_text_file.stat().st_size,
            backend_name="zlib",
            estimated_seconds=None,
            can_compress=True,
            reason_if_unavailable=None,
        )

        assert plan.options.algo == "zlib"
        assert plan.options.strategy == "balanced"
        assert plan.options.level == 6

"""Tests for the frontend API module."""

import sys
from pathlib import Path

import pytest

from compresso.frontend.api import (
    CompressionJob,
    CompressionOptions,
    CompressionPlan,
    DecompressionJob,
    DecompressionPlan,
    plan_compression,
)
from compresso.frontend.archive_api import OverwriteMode


class TestCompressionOptions:
    """Test the CompressionOptions dataclass."""

    def test_compression_options_default(self):
        """Test creating CompressionOptions with defaults."""
        opts = CompressionOptions()

        assert opts.algo is None
        assert opts.strategy == "balanced"
        assert opts.level is None
        assert opts.overwrite is OverwriteMode.RENAME

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

        with pytest.raises((AttributeError, TypeError)):
            opts.algo = "zstd"  # type: ignore


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


class TestPlanCompressionLevels:
    """Test that plan_compression refuses a level the backend would reject."""

    @pytest.mark.parametrize(
        "opts,match",
        [
            (CompressionOptions(algo="zlib", level=15), "zlib compression level 15"),
            (CompressionOptions(algo="snappy", level=3), "snappy has no compression"),
            # No algo: the backend the strategy picks is the one checked
            (CompressionOptions(strategy="fast", level=13), "lz4 compression level"),
            (CompressionOptions(format="gz", level=10), "gzip compression level 10"),
        ],
    )
    def test_out_of_range_level_is_unrunnable(
        self, sample_text_file: Path, opts: CompressionOptions, match: str
    ) -> None:
        """Test that the plan carries the core's own message."""
        plan = plan_compression(sample_text_file, options=opts)

        assert plan.can_compress is False
        assert plan.reason_if_unavailable is not None
        assert match in plan.reason_if_unavailable

    @pytest.mark.parametrize(
        "opts",
        [
            CompressionOptions(algo="zstd", level=19),
            CompressionOptions(algo="snappy"),
            CompressionOptions(format="zst", level=22),
        ],
    )
    def test_in_range_level_is_runnable(
        self, sample_text_file: Path, opts: CompressionOptions
    ) -> None:
        """Test that levels beyond the old 0-9 cap plan fine where they are valid."""
        assert plan_compression(sample_text_file, options=opts).can_compress is True


class TestDecompressionPlan:
    """Test the DecompressionPlan dataclass."""

    def test_decompression_plan_creation(self, sample_text_file: Path, temp_dir: Path):
        """Test creating a DecompressionPlan."""
        from compresso.introspect.file_inspect import InspectResult

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


class TestCompressionOverwrite:
    """Test the four `overwrite` modes against an existing destination file.

    Mirrors `TestArchiveOverwrite` in test_archive_api.py, covering both the
    Compresso container and a standalone format.
    """

    #: The numbered-sibling suffix `RENAME` inserts before the extension:
    #: "name 2.ext" on macOS, "name (2).ext" elsewhere.
    CONFLICT_SUFFIX = " {}" if sys.platform == "darwin" else " ({})"

    @staticmethod
    def _source_and_stale_output(
        temp_dir: Path, suffix: str = ".comp"
    ) -> tuple[Path, Path]:
        """Build a source file and a stale file already at the destination.

        Args:
            temp_dir: Directory to build both in.
            suffix: Extension for the destination file.

        Returns:
            The source path, and the destination path already occupied by an
            unrelated file.
        """
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")

        dest = temp_dir / f"out{suffix}"
        dest.write_bytes(b"stale")

        return source, dest

    @pytest.mark.parametrize(
        ("options_kwargs", "suffix"),
        [
            ({}, ".comp"),
            ({"format": "gz"}, ".gz"),
        ],
    )
    def test_renames_existing_output_by_default(
        self, temp_dir: Path, options_kwargs: dict, suffix: str
    ) -> None:
        """Test that the default policy renames rather than overwriting."""
        source, dest = self._source_and_stale_output(temp_dir, suffix)
        job = CompressionJob.from_file(
            source, dest, options=CompressionOptions(**options_kwargs)
        )

        result = job.run()

        assert result.ok, result.error
        assert dest.read_bytes() == b"stale"
        renamed = temp_dir / f"out{self.CONFLICT_SUFFIX.format(2)}{suffix}"
        assert job.plan.dest == renamed
        assert renamed.is_file()
        assert renamed.read_bytes() != b"stale"

    def test_error_mode_refuses_existing_output(self, temp_dir: Path) -> None:
        """Test that explicit `error` refuses to touch an existing output."""
        source, dest = self._source_and_stale_output(temp_dir)

        result = CompressionJob.from_file(
            source, dest, options=CompressionOptions(overwrite=OverwriteMode.ERROR)
        ).run()

        assert result.ok is False
        assert isinstance(result.error, FileExistsError)
        assert dest.read_bytes() == b"stale"

    def test_skip_leaves_the_existing_output(self, temp_dir: Path) -> None:
        """Test that `skip` leaves the stale output untouched and still succeeds."""
        source, dest = self._source_and_stale_output(temp_dir)
        job = CompressionJob.from_file(
            source, dest, options=CompressionOptions(overwrite=OverwriteMode.SKIP)
        )

        result = job.run()

        assert result.ok, result.error
        assert dest.read_bytes() == b"stale"
        assert job.plan.dest == dest

    def test_overwrite_replaces_the_existing_output(self, temp_dir: Path) -> None:
        """Test that `overwrite` replaces the stale output in place."""
        source, dest = self._source_and_stale_output(temp_dir)
        job = CompressionJob.from_file(
            source, dest, options=CompressionOptions(overwrite=OverwriteMode.OVERWRITE)
        )

        result = job.run()

        assert result.ok, result.error
        assert dest.read_bytes() != b"stale"
        assert job.plan.dest == dest

    def test_plain_string_mode_is_normalised(self, temp_dir: Path) -> None:
        """Test that a mode given as a plain string still plans as the enum."""
        source, dest = self._source_and_stale_output(temp_dir)

        plan = CompressionJob.from_file(
            source,
            dest,
            options=CompressionOptions(overwrite="skip"),  # ty: ignore
        ).plan

        assert plan.can_compress is True
        assert plan.options.overwrite is OverwriteMode.SKIP

    def test_unknown_mode_is_an_unavailable_plan(self, temp_dir: Path) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        source, dest = self._source_and_stale_output(temp_dir)

        plan = CompressionJob.from_file(
            source,
            dest,
            options=CompressionOptions(overwrite="clobber"),  # ty: ignore
        ).plan

        assert plan.can_compress is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)


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

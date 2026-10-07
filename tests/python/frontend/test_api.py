"""Tests for the frontend API module."""

from pathlib import Path

import pytest

from compresso.frontend.api import (
    CompressionJob,
    CompressionOptions,
    DecompressionJob,
    plan_compression,
)
from compresso.frontend.archive_api import OverwriteMode

from ..helpers import renamed


class TestCompressionOptions:
    """Test the CompressionOptions dataclass."""

    def test_compression_options_default(self):
        """Test creating CompressionOptions with defaults."""
        opts = CompressionOptions()

        assert opts.algo is None
        assert opts.strategy == "balanced"
        assert opts.level is None
        assert opts.overwrite is OverwriteMode.RENAME

    def test_compression_options_immutable(self):
        """Test that CompressionOptions is immutable."""
        opts = CompressionOptions(algo="zlib", strategy="fast", level=1)

        with pytest.raises((AttributeError, TypeError)):
            opts.algo = "zstd"  # type: ignore


class TestPlanCompression:
    """Test what plan_compression works out before anything is written."""

    def test_defaults_plan_a_runnable_job(self, sample_text_file: Path) -> None:
        """Test that default options resolve a backend, destination and size."""
        plan = plan_compression(sample_text_file)

        assert plan.can_compress is True
        assert plan.reason_if_unavailable is None

        # The backend the default "balanced" strategy picks
        assert plan.backend_name == "zstd"
        assert plan.dest == sample_text_file.with_name(f"{sample_text_file.name}.comp")
        assert plan.input_size == sample_text_file.stat().st_size
        assert plan.estimated_seconds is not None
        assert plan.estimated_seconds > 0


class TestDecompressionJob:
    """Test the DecompressionJob class."""

    def test_from_file_restores_the_original_name(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that with no destination, the .comp suffix is dropped."""
        out_dir = temp_dir / "out"
        out_dir.mkdir()
        compressed = out_dir / f"{sample_text_file.name}.comp"
        assert CompressionJob.from_file(sample_text_file, compressed).run().ok

        job = DecompressionJob.from_file(compressed)

        assert job.plan.can_run is True
        assert job.plan.dest == out_dir / sample_text_file.name
        assert job.run().ok
        assert job.plan.dest.read_bytes() == sample_text_file.read_bytes()


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
            (CompressionOptions(strategy="fsat"), "Unknown strategy: fsat"),
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


class TestCompressionOverwrite:
    """Test the four `overwrite` modes against an existing destination file.

    Mirrors `TestArchiveOverwrite` in test_archive_api.py, covering both the
    Compresso container and a standalone format.
    """

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
        sibling = temp_dir / renamed(f"out{suffix}")
        assert job.plan.dest == sibling
        assert sibling.is_file()
        assert sibling.read_bytes() != b"stale"

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


class TestDecompressionOverwrite:
    """Test the four `overwrite` modes when decompressing onto an existing file."""

    @pytest.mark.parametrize("fmt", ["comp", "gz"])
    @pytest.mark.parametrize(
        ("mode", "ok", "replaced", "to_sibling"),
        [
            (OverwriteMode.RENAME, True, False, True),
            (OverwriteMode.ERROR, False, False, False),
            (OverwriteMode.SKIP, True, False, False),
            (OverwriteMode.OVERWRITE, True, True, False),
        ],
    )
    def test_mode_against_an_existing_output(
        self,
        temp_dir: Path,
        fmt: str,
        mode: OverwriteMode,
        ok: bool,
        replaced: bool,
        to_sibling: bool,
    ) -> None:
        """Test each mode for the Compresso container and a standalone format."""
        source = temp_dir / "a.txt"
        source.write_bytes(b"hello compresso")
        compressed = temp_dir / f"a.txt.{fmt}"
        options = CompressionOptions(format=None if fmt == "comp" else fmt)
        assert CompressionJob.from_file(source, compressed, options=options).run().ok
        dest = temp_dir / "out.txt"
        dest.write_bytes(b"stale")

        job = DecompressionJob.from_file(compressed, dest, overwrite=mode)
        result = job.run()

        assert result.ok is ok, result.error
        if not ok:
            assert isinstance(result.error, FileExistsError)
        assert dest.read_bytes() == (b"hello compresso" if replaced else b"stale")
        sibling = temp_dir / renamed("out.txt")
        assert job.plan.dest == (sibling if to_sibling else dest)
        if to_sibling:
            assert sibling.read_bytes() == b"hello compresso"

    def test_unknown_mode_is_an_unavailable_plan(
        self, sample_text_file: Path, temp_dir: Path
    ) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        compressed = temp_dir / "a.comp"
        assert CompressionJob.from_file(sample_text_file, compressed).run().ok

        plan = DecompressionJob.from_file(
            compressed,
            overwrite="clobber",  # ty: ignore
        ).plan

        assert plan.can_run is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)

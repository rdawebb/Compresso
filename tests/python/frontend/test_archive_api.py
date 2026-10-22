"""Tests for the frontend archive API module."""

import sys
import tarfile
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from compresso import ExtractionPolicyError
from compresso.frontend._job import JobResult
from compresso.frontend.archive_api import (
    ArchiveEntry,
    ArchiveJob,
    ArchiveOptions,
    ArchivePlan,
    ExtractJob,
    ExtractOptions,
    ExtractPlan,
    OverwriteMode,
    plan_archive,
    plan_extraction,
)

from ..helpers import dir_entry, file_entry, link_entry, make_tar, renamed, umask


class TestPlanArchive:
    """Test the plan_archive planner."""

    def test_archive_options_default(self) -> None:
        """Test creating ArchiveOptions with defaults."""
        opts = ArchiveOptions()

        assert opts.format is None
        assert opts.compression_level is None
        assert opts.overwrite is OverwriteMode.RENAME

    @pytest.mark.parametrize(
        ("name", "fmt", "expected"),
        [
            ("out.tar.zst", None, "tar.zst"),
            ("out.bin", None, "zip"),
            ("out.tar.zst", "tar", "tar"),
        ],
        ids=["suffix", "unrecognised-suffix", "explicit-wins"],
    )
    def test_format_comes_from_the_output_name(
        self,
        small_file: Path,
        tmp_path: Path,
        name: str,
        fmt: str | None,
        expected: str,
    ) -> None:
        """Test that an unset format is read from the output's suffix, else zip."""
        plan = plan_archive([small_file], tmp_path / name, ArchiveOptions(format=fmt))

        assert plan.can_run, plan.reason_if_unavailable
        assert plan.options.format == expected

    def test_plan_archive_valid(self, sample_text_file: Path, tmp_path: Path) -> None:
        """Test that a plan over existing sources can run and sums input size."""
        out = tmp_path / "out.tar.zst"
        plan = plan_archive([sample_text_file], out)

        assert isinstance(plan, ArchivePlan)
        assert plan.can_run is True
        assert plan.reason_if_unavailable is None
        assert plan.entry_count == 1
        assert plan.total_input_size == sample_text_file.stat().st_size

    # The level row: the stage that compresses decides which levels are valid
    @pytest.mark.usefixtures("sample_text_file")
    @pytest.mark.parametrize(
        ("sources", "opts", "match"),
        [
            ([], ArchiveOptions(), "No source paths"),
            (["nope.txt"], ArchiveOptions(), "does not exist"),
            (["sample.txt"], ArchiveOptions(format="gz"), "does not support archives"),
            (
                ["sample.txt"],
                ArchiveOptions(format="tar.zst", compression_level=23),
                "zstd compression level 23",
            ),
        ],
        ids=["no-sources", "missing-source", "non-archive-format", "level"],
    )
    def test_plan_archive_unrunnable(
        self, tmp_path: Path, sources: list[str], opts: ArchiveOptions, match: str
    ) -> None:
        """Test that a plan that cannot run says why."""
        paths = [tmp_path / name for name in sources]
        plan = plan_archive(paths, tmp_path / "out.tar.zst", opts)

        assert plan.can_run is False
        assert match in (plan.reason_if_unavailable or "")


class TestArchiveJob:
    """Test the ArchiveJob class."""

    def test_run_on_unavailable_plan_returns_failed_result(
        self, tmp_path: Path
    ) -> None:
        """Test that run() never raises: an unrunnable plan yields a failed JobResult."""
        job = ArchiveJob.from_paths([], tmp_path / "out.tar.zst")
        result = job.run()

        assert isinstance(result, JobResult)
        assert result.ok is False
        assert result.error is not None

    def test_level_zero_is_not_the_default(self, tmp_path: Path) -> None:
        """Test that level 0 reaches the codec rather than reading as "unset"."""
        source = tmp_path / "text.txt"
        source.write_text("a compressible line of text\n" * 4000)

        def archive_size(level: int | None) -> int:
            out = tmp_path / f"level_{level}.tar.gz"
            opts = ArchiveOptions(format="tar.gz", compression_level=level)
            assert ArchiveJob.from_paths([source], out, opts).run().ok
            return out.stat().st_size

        # gzip level 0 stores the data, so it is far larger than the default
        assert archive_size(0) > archive_size(None) * 10


class TestArchiveOverwrite:
    """Test the four `overwrite` modes against an existing destination archive."""

    @staticmethod
    def _source_and_stale_output(
        tmp_path: Path, name: str = "out.tar"
    ) -> tuple[Path, Path]:
        """Build a source file and a stale file already at the archive's path.

        Args:
            tmp_path: Directory to build both in.
            name: The archive's file name.

        Returns:
            The source path, and the archive path already occupied by an
            unrelated file.
        """
        source = tmp_path / "a.txt"
        source.write_bytes(b"hello compresso")

        archive_path = tmp_path / name
        archive_path.write_bytes(b"stale")

        return source, archive_path

    @pytest.mark.parametrize(
        ("mode", "ok", "output", "written"),
        [
            (None, True, renamed("out.tar"), True),
            (OverwriteMode.ERROR, False, "out.tar", False),
            (OverwriteMode.SKIP, True, "out.tar", False),
            (OverwriteMode.OVERWRITE, True, "out.tar", True),
        ],
        ids=["rename-by-default", "error", "skip", "overwrite"],
    )
    def test_existing_archive_follows_the_mode(
        self,
        tmp_path: Path,
        mode: OverwriteMode | None,
        ok: bool,
        output: str,
        written: bool,
    ) -> None:
        """Test where the archive goes, and that only OVERWRITE replaces the stale one."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        kwargs: dict[str, Any] = {} if mode is None else {"overwrite": mode}
        job = ArchiveJob.from_paths(
            [source], archive_path, ArchiveOptions(format="tar", **kwargs)
        )

        result = job.run()

        assert result.ok is ok, result.error
        assert ok or isinstance(result.error, FileExistsError)
        assert job.plan.output == tmp_path / output
        assert (job.plan.output.read_bytes() != b"stale") is written
        assert (archive_path.read_bytes() == b"stale") is (
            mode is not OverwriteMode.OVERWRITE
        )

    @pytest.mark.parametrize(
        ("name", "fmt"), [("out.tar.zst", "tar.zst"), ("OUT.TAR.GZ", "tar.gz")]
    )
    def test_rename_keeps_a_compound_extension_intact(
        self, tmp_path: Path, name: str, fmt: str
    ) -> None:
        """Test that "out.tar.zst" renames to "out 2.tar.zst", not "out.tar 2.zst", in either case.

        `create_archive` writes a codec format's output only at the very end,
        through a separate temp-file stage - a case the plain single-extension
        rename doesn't exercise.
        """
        source, archive_path = self._source_and_stale_output(tmp_path, name)

        job = ArchiveJob.from_paths([source], archive_path, ArchiveOptions(format=fmt))
        result = job.run()

        assert result.ok, result.error
        sibling = tmp_path / renamed(name)
        assert job.plan.output == sibling
        assert sibling.is_file()

    def test_rename_increments_through_repeated_conflicts(self, tmp_path: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        source, archive_path = self._source_and_stale_output(tmp_path)
        options = ArchiveOptions(format="tar", overwrite=OverwriteMode.RENAME)

        assert ArchiveJob.from_paths([source], archive_path, options).run().ok
        result = ArchiveJob.from_paths([source], archive_path, options).run()

        assert result.ok, result.error
        assert (tmp_path / renamed("out.tar")).exists()
        assert (tmp_path / renamed("out.tar", 3)).exists()


class TestExtractJob:
    """Test the ExtractJob class."""

    def test_missing_archive_is_unavailable_and_never_raises(
        self, tmp_path: Path
    ) -> None:
        """Test that a missing archive plans as unrunnable, and run() returns a failed result."""
        job = ExtractJob.from_archive(tmp_path / "missing.tar.zst")

        assert isinstance(job.plan, ExtractPlan)
        assert job.plan.can_run is False
        result = job.run()
        assert isinstance(result, JobResult)
        assert result.ok is False

    def test_output_dir_defaults_to_the_archives_own_directory(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test that omitting output_dir plans beside the archive, not in the cwd."""
        nested: Path = tmp_path / "nested"
        nested.mkdir()
        monkeypatch.chdir(tmp_path)

        plan = ExtractJob.from_archive(nested / "missing.tar.zst").plan

        assert plan.output_dir == nested.resolve()

    def test_output_dir_default_ignores_a_relative_archive_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test that a relative archive path still resolves to a real directory."""
        monkeypatch.chdir(tmp_path)

        plan = ExtractJob.from_archive("missing.tar.zst").plan

        assert plan.output_dir == tmp_path.resolve()


class TestArchiveRoundTrip:
    """Test end-to-end archive -> extract round-trip."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_round_trip(self, tmp_path: Path, fmt: str) -> None:
        """Test that a nested tree with non-ASCII names comes back as it went in."""
        src = tmp_path / "données"
        (src / "日本語").mkdir(parents=True)
        (src / "café.txt").write_bytes(b"contenu")
        (src / "日本語" / "leaf.txt").write_bytes(b"content")
        archive_path = tmp_path / f"archivé.{fmt}"

        options = ArchiveOptions(format=fmt)
        result = ArchiveJob.from_paths([src], archive_path, options).run()
        assert result.ok, result.error

        extract_job = ExtractJob.from_archive(archive_path, tmp_path / "sortie")
        assert all(isinstance(e, ArchiveEntry) for e in extract_job.list_contents())
        result = extract_job.run()
        assert result.ok, result.error

        out = tmp_path / "sortie" / "données"
        assert (out / "café.txt").read_bytes() == b"contenu"
        assert (out / "日本語" / "leaf.txt").read_bytes() == b"content"


@pytest.mark.usefixtures("needs_symlinks")
class TestArchivingSymlinks:
    """Test that a symlink is archived as a symlink, not as what it points at.

    Written against an uncompressed tar, checked both through `tarfile` and
    through the entries an extraction plan lists.
    """

    def test_symlink_is_stored_as_a_link(self, tmp_path: Path, monkeypatch) -> None:
        """Test that a symlink becomes a link entry rather than a copy of its target."""
        monkeypatch.chdir(tmp_path)
        src = Path("tree")
        src.mkdir()
        (src / "real.txt").write_bytes(b"real content")
        (src / "alias.txt").symlink_to("real.txt")

        archive_path = Path("tree.tar")
        options = ArchiveOptions(format="tar")
        assert ArchiveJob.from_paths([src], archive_path, options).run().ok

        with tarfile.open(archive_path) as tar:
            members = {m.name: m for m in tar}
        assert members["tree/alias.txt"].issym()
        assert members["tree/alias.txt"].linkname == "real.txt"
        assert members["tree/real.txt"].isreg()

        entries = {
            e.path: e for e in ExtractJob.from_archive(archive_path).plan.entries
        }
        assert entries["tree/alias.txt"].is_symlink
        assert entries["tree/alias.txt"].link_target == "real.txt"
        assert not entries["tree/real.txt"].is_symlink
        assert entries["tree/real.txt"].link_target is None

    def test_symlink_loop_does_not_recurse(self, tmp_path: Path, monkeypatch) -> None:
        """Test that a symlink pointing at its own parent terminates the walk."""
        monkeypatch.chdir(tmp_path)
        src = Path("tree")
        src.mkdir()
        (src / "a.txt").write_bytes(b"content")
        (src / "loop").symlink_to("..", target_is_directory=True)

        archive_path = Path("loop.tar")
        options = ArchiveOptions(format="tar")
        result = ArchiveJob.from_paths([src], archive_path, options).run()
        assert result.ok, result.error

        with tarfile.open(archive_path) as tar:
            names = sorted(m.name for m in tar)
        assert names == ["tree", "tree/a.txt", "tree/loop"]


class TestExtractionRefusesDeniedEntryTypes:
    """Test that the default policy refuses symlink entries."""

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        """Test that a policy refusal comes back as a failed result, not a raise."""
        archive_path = tmp_path / "typed.tar"
        make_tar(archive_path, link_entry("entry", "target", kind=tarfile.SYMTYPE))

        result = ExtractJob.from_archive(archive_path, tmp_path / "out").run()

        assert result.ok is False
        assert isinstance(result.error, ExtractionPolicyError)
        assert "contains symlink, but policy denies it" in str(result.error)

    def test_symlink_mode_reaches_the_policy(self, tmp_path: Path) -> None:
        """Test that `symlinks`, given as a plain string, is passed through."""
        archive_path = tmp_path / "link.tar"
        make_tar(archive_path, link_entry("entry", "target", kind=tarfile.SYMTYPE))
        out = tmp_path / "out"

        options = ExtractOptions(symlinks="file")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        result = ExtractJob.from_archive(archive_path, out, options=options).run()

        assert result.ok is True
        assert (out / "entry").read_bytes() == b"target"

    def test_zip_symlink_becomes_a_file_by_default(self, tmp_path: Path) -> None:
        """Test that leaving `symlinks` unset takes zip's own default."""
        archive_path = tmp_path / "link.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            info = zipfile.ZipInfo("entry")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, b"target")
        out = tmp_path / "out"

        result = ExtractJob.from_archive(archive_path, out).run()

        assert result.ok is True
        assert (out / "entry").read_bytes() == b"target"

    def test_unknown_symlink_mode_is_unavailable(self, tmp_path: Path) -> None:
        """Test that a misspelt mode is refused when planning, naming the field."""
        options = ExtractOptions(symlinks="follow")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        plan = plan_extraction(tmp_path / "a.tar", tmp_path, options=options)

        assert plan.can_run is False
        assert "Unknown symlinks mode: 'follow'" in (plan.reason_if_unavailable or "")


class TestExtractionOverwrite:
    """Test the four `overwrite` modes against an existing destination."""

    @staticmethod
    def _archive_and_stale_output(tmp_path: Path) -> tuple[Path, Path]:
        """Build a one-entry archive and an output dir already holding that name.

        Args:
            tmp_path: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = tmp_path / "one.tar"
        make_tar(archive_path, file_entry("a.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "a.txt").write_bytes(b"original")

        return archive_path, out_dir

    @pytest.mark.parametrize(
        ("mode", "ok", "contents"),
        [
            (None, True, {"a.txt": b"original", renamed("a.txt"): b"x"}),
            (OverwriteMode.ERROR, False, {"a.txt": b"original"}),
            (OverwriteMode.SKIP, True, {"a.txt": b"original"}),
            (OverwriteMode.OVERWRITE, True, {"a.txt": b"x"}),
        ],
        ids=["rename-by-default", "error", "skip", "overwrite"],
    )
    def test_file_clash_follows_the_mode(
        self,
        tmp_path: Path,
        mode: OverwriteMode | None,
        ok: bool,
        contents: dict[str, bytes | None],
    ) -> None:
        """Test what each mode leaves when a file is already at the entry's path."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)
        kwargs: dict[str, Any] = {} if mode is None else {"overwrite": mode}

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(**kwargs)
        ).run()

        assert result.ok is ok, result.error
        assert ok or isinstance(result.error, FileExistsError)
        assert _contents(out_dir) == contents

    def test_rename_increments_through_repeated_conflicts(self, tmp_path: Path) -> None:
        """Test that a third clash gets " 3", not another " 2"."""
        archive_path, out_dir = self._archive_and_stale_output(tmp_path)
        options = ExtractOptions(overwrite=OverwriteMode.RENAME)

        assert ExtractJob.from_archive(archive_path, out_dir, options=options).run().ok
        result = ExtractJob.from_archive(archive_path, out_dir, options=options).run()

        assert result.ok, result.error
        assert (out_dir / renamed("a.txt")).exists()
        assert (out_dir / renamed("a.txt", 3)).exists()

    @staticmethod
    def _archive_with_dir_and_blocking_file(tmp_path: Path) -> tuple[Path, Path]:
        """Build a one-directory-entry archive and a same-named file blocking it.

        Args:
            tmp_path: Directory to build both in.

        Returns:
            The archive path and the output directory.
        """
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        return archive_path, out_dir

    @pytest.mark.parametrize(
        ("mode", "ok", "contents"),
        [
            (None, True, {"d": b"blocking file", renamed("d"): None}),
            (OverwriteMode.ERROR, False, {"d": b"blocking file"}),
            (OverwriteMode.SKIP, True, {"d": b"blocking file"}),
            (OverwriteMode.OVERWRITE, True, {"d": None}),
        ],
        ids=["rename-by-default", "error", "skip", "overwrite"],
    )
    def test_dir_clash_with_a_file_follows_the_mode(
        self,
        tmp_path: Path,
        mode: OverwriteMode | None,
        ok: bool,
        contents: dict[str, bytes | None],
    ) -> None:
        """Test what each mode leaves when a file blocks a directory entry."""
        archive_path, out_dir = self._archive_with_dir_and_blocking_file(tmp_path)
        kwargs: dict[str, Any] = {} if mode is None else {"overwrite": mode}

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(**kwargs)
        ).run()

        assert result.ok is ok, result.error
        assert ok or "already exists" in str(result.error)
        assert _contents(out_dir) == contents

    def test_dir_clash_with_a_file_renames_nested_entries_too(
        self, tmp_path: Path
    ) -> None:
        """Test that entries nested under the renamed directory follow it."""
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"), file_entry("d/inside.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").write_bytes(b"blocking file")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        assert (sibling / "inside.txt").read_bytes() == b"x"
        # The original "d" is untouched, and nothing was created back under it
        assert (out_dir / "d").read_bytes() == b"blocking file"

    def test_doubly_nested_dir_clash_composes_with_parent_rename(
        self, tmp_path: Path
    ) -> None:
        """Test that a second clash below an already-renamed ancestor composes."""
        archive_path = tmp_path / "dir.tar"
        make_tar(
            archive_path,
            dir_entry("a"),
            file_entry(f"{renamed('a')}/b"),
            dir_entry("a/b"),
            file_entry("a/b/inside.txt"),
        )

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "a").write_bytes(b"blocks a")

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(overwrite=OverwriteMode.RENAME),
        ).run()

        assert result.ok, result.error
        renamed_a = out_dir / renamed("a")
        assert (renamed_a / "b").read_bytes() == b"x"  # the unrelated entry
        renamed_b = renamed_a / renamed("b")
        assert renamed_b.is_dir()
        assert (renamed_b / "inside.txt").read_bytes() == b"x"
        # Nothing was planted back under the archive's own, un-renamed "a/b"
        assert not (out_dir / "a" / "b").exists()

    def test_dir_clash_with_an_existing_dir_renames_by_default(
        self, tmp_path: Path
    ) -> None:
        """Test that re-extracting over an existing directory renames the
        directory itself rather than merging into it."""
        archive_path = tmp_path / "dir.tar"
        make_tar(archive_path, dir_entry("d"), file_entry("d/inside.txt"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d").mkdir()
        (out_dir / "d" / "inside.txt").write_bytes(b"original")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        # The original directory and its contents are untouched
        assert (out_dir / "d" / "inside.txt").read_bytes() == b"original"

        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        # The nested file lands under the renamed directory with its own
        # name unchanged - not doubled up or given its own " 2" suffix
        assert (sibling / "inside.txt").read_bytes() == b"x"

    def test_dir_clash_with_an_existing_dir_renames_subdirs_too(
        self, tmp_path: Path
    ) -> None:
        """Test that subdirectories nested under a clashing directory follow
        the rename instead of being merged into the pre-existing tree, which
        would otherwise leave the files beneath them doubled up."""
        archive_path = tmp_path / "dir.tar"
        make_tar(
            archive_path,
            dir_entry("d"),
            dir_entry("d/sub"),
            file_entry("d/sub/inside.txt"),
        )

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "d" / "sub").mkdir(parents=True)
        (out_dir / "d" / "sub" / "inside.txt").write_bytes(b"original")

        result = ExtractJob.from_archive(archive_path, out_dir).run()

        assert result.ok, result.error
        # The original tree is untouched - no doubled-up file inside it
        assert sorted(p.name for p in (out_dir / "d").iterdir()) == ["sub"]
        assert sorted(p.name for p in (out_dir / "d" / "sub").iterdir()) == [
            "inside.txt"
        ]
        assert (out_dir / "d" / "sub" / "inside.txt").read_bytes() == b"original"

        sibling = out_dir / renamed("d")
        assert sibling.is_dir()
        assert sorted(p.name for p in sibling.iterdir()) == ["sub"]
        assert (sibling / "sub" / "inside.txt").read_bytes() == b"x"


def _contents(directory: Path) -> dict[str, bytes | None]:
    """What `directory` holds, one level deep.

    Args:
        directory: The directory to read.

    Returns:
        Each entry's bytes by name, or None for a subdirectory.
    """
    return {p.name: None if p.is_dir() else p.read_bytes() for p in directory.iterdir()}


def _archive_plan(tmp_path: Path, overwrite: str) -> ArchivePlan:
    """Plan archiving one file with `overwrite` given as a plain string.

    Args:
        tmp_path: Directory to build the source in.
        overwrite: The overwrite mode, deliberately not an OverwriteMode.

    Returns:
        The archive plan.
    """
    source = tmp_path / "a.txt"
    source.write_bytes(b"hello compresso")
    options = ArchiveOptions(format="tar", overwrite=overwrite)  # ty: ignore

    return ArchiveJob.from_paths([source], tmp_path / "out.tar", options).plan


def _extract_plan(tmp_path: Path, overwrite: str) -> ExtractPlan:
    """Plan extracting a one-entry tar with `overwrite` given as a plain string.

    Args:
        tmp_path: Directory to build the archive in.
        overwrite: The overwrite mode, deliberately not an OverwriteMode.

    Returns:
        The extraction plan.
    """
    archive_path = tmp_path / "one.tar"
    make_tar(archive_path, file_entry("a.txt"))
    options = ExtractOptions(overwrite=overwrite)  # ty: ignore

    return ExtractJob.from_archive(archive_path, tmp_path / "out", options=options).plan


PLANNERS = pytest.mark.parametrize(
    "plan_with", [_archive_plan, _extract_plan], ids=["archive", "extract"]
)


class TestOverwriteModeNames:
    """Test how both archive jobs take an overwrite mode given by name."""

    @PLANNERS
    def test_plain_string_mode_is_normalised(
        self,
        tmp_path: Path,
        plan_with: Callable[[Path, str], ArchivePlan | ExtractPlan],
    ) -> None:
        """Test that a mode given as a plain string still plans as the enum."""
        plan = plan_with(tmp_path, "skip")

        assert plan.can_run is True
        assert plan.options.overwrite is OverwriteMode.SKIP

    @PLANNERS
    def test_unknown_mode_is_an_unavailable_plan(
        self,
        tmp_path: Path,
        plan_with: Callable[[Path, str], ArchivePlan | ExtractPlan],
    ) -> None:
        """Test that a mode outside the four names never reaches the C layer."""
        plan = plan_with(tmp_path, "clobber")

        assert plan.can_run is False
        assert "Unknown overwrite mode" in str(plan.reason_if_unavailable)


class TestExtractionMetadata:
    """Test that the metadata options reach the policy."""

    @pytest.mark.parametrize("opt_in", [False, True])
    def test_existing_directory_metadata_follows_the_option(
        self, tmp_path: Path, opt_in: bool
    ) -> None:
        """Test that only the opt-in re-dates a directory that already exists."""
        archive_path = tmp_path / "meta.tar"
        make_tar(
            archive_path, dir_entry("d", mtime=1_000_000_000), file_entry("d/a.txt")
        )
        out_dir = tmp_path / "out"
        (out_dir / "d").mkdir(parents=True)

        result = ExtractJob.from_archive(
            archive_path,
            out_dir,
            options=ExtractOptions(
                overwrite=OverwriteMode.OVERWRITE, overwrite_dir_metadata=opt_in
            ),
        ).run()

        assert result.ok, result.error
        restored = (out_dir / "d").stat().st_mtime == pytest.approx(
            1_000_000_000, abs=2
        )
        assert restored == opt_in

    def test_preserve_timestamps_off_uses_the_current_time(
        self, tmp_path: Path
    ) -> None:
        """Test that the stored mtime is ignored when the flag is off."""
        archive_path = tmp_path / "meta.tar"
        make_tar(archive_path, file_entry("a.txt", mode=0o644, mtime=1_000_000_000))
        out_dir = tmp_path / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_timestamps=False)
        ).run()

        assert result.ok, result.error
        assert (out_dir / "a.txt").stat().st_mtime > 1_000_000_000

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX permission bits"
    )
    def test_preserve_permissions_off_leaves_the_created_mode(
        self, tmp_path: Path
    ) -> None:
        """Test that the stored mode is not applied when the flag is off."""
        archive_path = tmp_path / "meta.tar"
        make_tar(archive_path, file_entry("a.txt", mode=0o600, mtime=1_000_000_000))
        out_dir = tmp_path / "out"

        result = ExtractJob.from_archive(
            archive_path, out_dir, options=ExtractOptions(preserve_permissions=False)
        ).run()

        assert result.ok, result.error
        created = (out_dir / "a.txt").stat().st_mode & 0o777
        assert created == 0o666 & ~umask()


BIG_PAYLOAD = b"compressible " * 4000


@dataclass
class ArchivedTree:
    """A small tree, and the entries planned for it in each archive format."""

    tree: Path
    entries: dict[str, dict[str, ArchiveEntry]]


class TestArchiveEntryMetadata:
    """Test what `plan_extraction` reports about each entry."""

    @pytest.fixture(scope="class")
    @classmethod
    def archived(cls, tmp_path_factory: pytest.TempPathFactory) -> ArchivedTree:
        """Archive a small tree once per format, and plan each extraction.

        Shared by the whole class, so tests must only read it; each format's
        entries are keyed by path without a trailing separator.
        """
        tmp = tmp_path_factory.mktemp("entry_metadata")
        tree = tmp / "tree"
        tree.mkdir()
        (tree / "big.bin").write_bytes(BIG_PAYLOAD)
        (tree / "small.txt").write_text("hello")
        (tree / "nested").mkdir()

        entries = {}
        for fmt in ("tar", "tar.zst", "zip"):
            archive = tmp / f"out.{fmt}"
            options = ArchiveOptions(format=fmt)
            result = ArchiveJob.from_paths([tree], archive, options).run()
            assert result.ok, result.error

            plan = plan_extraction(archive, tmp / f"dest_{fmt}")
            assert plan.can_run, plan.reason_if_unavailable
            entries[fmt] = {e.path.rstrip("/"): e for e in plan.entries}

        return ArchivedTree(tree, entries)

    @pytest.mark.parametrize("fmt", ["tar", "tar.zst", "zip"])
    def test_entries_are_described(self, archived: ArchivedTree, fmt: str) -> None:
        """Test that mtime, mode, size and type cross the boundary, not their defaults.

        The CLI sums the sizes to decide whether to show a progress bar.
        """
        big = archived.entries[fmt]["tree/big.bin"]
        nested = archived.entries[fmt]["tree/nested"]

        # Written moments ago, so it cannot be far from now
        assert abs(big.mtime - (archived.tree / "big.bin").stat().st_mtime) < 60
        assert big.mode & 0o400, "expected a readable file mode"
        assert nested.mode & 0o100, "expected a searchable dir mode"
        assert (big.size, big.is_dir) == (len(BIG_PAYLOAD), False)
        assert (nested.size, nested.is_dir) == (0, True)

    def test_zip_reports_per_entry_compression(self, archived: ArchivedTree) -> None:
        """Test that zip compresses each entry, so it can say how well, and the CRC it stored."""
        entry = archived.entries["zip"]["tree/big.bin"]

        assert entry.compressed_size is not None
        assert 0 < entry.compressed_size < entry.size
        assert entry.crc == zlib.crc32(BIG_PAYLOAD)
        assert entry.method is not None

    def test_tar_omits_per_entry_compression(self, archived: ArchivedTree) -> None:
        """Test that tar compresses the whole stream, so there is nothing to report."""
        entry = archived.entries["tar"]["tree/big.bin"]

        assert entry.compressed_size is None
        assert entry.crc is None
        assert entry.method is None

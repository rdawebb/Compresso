"""Tests for creating archives."""

import contextlib
import logging
import os
import socket
import stat
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import orjson
import pytest

from compresso import BackendError, _core

from .helpers import ARCHIVE_AND_LIST, run_child


class TestArchiveErrorTypes:
    """Test that archive failures say whether the input, the OS or policy failed."""

    # libzip refuses a name that isn't UTF-8, which only Linux allows
    @pytest.mark.skipif(sys.platform != "linux", reason="needs non-UTF-8 names")
    @pytest.mark.parametrize("kind", ["directory", "symlink"])
    def test_entry_refused_inside_a_tree_fails_the_archive(
        self, tmp_path: Path, kind: str
    ) -> None:
        """Test that a nested entry the writer refuses fails the whole job."""
        tree = tmp_path / "tree"
        tree.mkdir()
        bad_name = os.fsencode(tree) + b"/bad\xff"
        if kind == "directory":
            os.mkdir(bad_name)
        else:
            os.symlink(b"target", bad_name)
        output = tmp_path / "out.zip"

        with pytest.raises(BackendError, match=f"Failed to add {kind}"):
            _core.create_archive(str(output), "zip", [str(tree)])

        assert not output.exists()

    @pytest.mark.parametrize("fmt", ["tar", "zip", "tar.gz"])
    def test_failed_walk_reports_its_own_error(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that abandoning the half-built archive doesn't replace the error."""
        missing = tmp_path / "missing"
        output = tmp_path / f"out.{fmt}"

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(
                str(output), fmt, [str(sample_text_file), str(missing)]
            )

        assert info.value.filename == str(missing)
        assert sorted(p.name for p in tmp_path.iterdir()) == [sample_text_file.name]

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_unwritable_destination_is_an_os_error(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that a destination in a missing directory carries its errno."""
        dest = tmp_path / "missing" / f"out.{fmt}"

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(str(dest), fmt, [str(sample_text_file)])

        assert info.value.filename == str(dest)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
class TestArchiveSpecialFiles:
    """Test that tar stores FIFOs and devices, zip skips them, and neither reads them."""

    def test_zip_skips_a_fifo(self, tmp_path: Path) -> None:
        """Test that a FIFO in the tree, or named directly, is left out of a zip."""
        fmt = "zip"
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a")
        os.mkfifo(tree / "pipe")
        os.mkfifo(tmp_path / "named_pipe")
        output = tmp_path / f"out.{fmt}"

        result = run_child(
            ARCHIVE_AND_LIST, str(output), fmt, str(tree), str(tmp_path / "named_pipe")
        )

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == ["tree/", "tree/a.txt"]
        for skipped in (tree / "pipe", tmp_path / "named_pipe"):
            assert f"WARNING Skipped {skipped}:" in result.stderr

    def test_nothing_left_to_archive_gives_an_empty_archive(
        self, tmp_path: Path
    ) -> None:
        """Test that an archive whose only input is skipped is written empty."""
        os.mkfifo(tmp_path / "pipe")
        output = tmp_path / "out.zip"

        result = run_child(ARCHIVE_AND_LIST, str(output), "zip", str(tmp_path / "pipe"))

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == []
        with zipfile.ZipFile(output) as zf:
            assert zf.namelist() == []

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz"])
    def test_tar_stores_a_fifo_not_its_data(self, tmp_path: Path, fmt: str) -> None:
        """Test that a FIFO is stored as a FIFO entry, as GNU tar stores one."""
        tree = tmp_path / "tree"
        tree.mkdir()
        os.mkfifo(tree / "pipe")
        output = tmp_path / f"out.{fmt}"

        result = run_child(ARCHIVE_AND_LIST, str(output), fmt, str(tree))

        assert result.returncode == 0, result.stderr
        with tarfile.open(output) as tf:
            member = tf.getmember("tree/pipe")
        assert member.isfifo()
        assert member.size == 0
        assert member.mode == stat.S_IMODE((tree / "pipe").stat().st_mode)

    @pytest.mark.skipif(not os.path.exists("/dev/null"), reason="needs /dev/null")
    def test_tar_stores_a_device_with_its_number(self, tmp_path: Path) -> None:
        """Test that a character device keeps its major and minor numbers."""
        output = tmp_path / "out.tar"
        rdev = os.stat("/dev/null").st_rdev

        _core.create_archive(str(output), "tar", ["/dev/null"])

        with tarfile.open(output) as tf:
            member = tf.getmember("null")
        assert member.ischr()
        assert (member.devmajor, member.devminor) == (os.major(rdev), os.minor(rdev))

    def test_tar_skips_a_socket(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that a socket is left out of a tar with a warning, as GNU tar does."""
        # A relative name, since a socket path's length is capped near 100 bytes
        monkeypatch.chdir(tmp_path)
        Path("tree").mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind("tree/sock")
            with caplog.at_level(logging.WARNING):
                _core.create_archive("out.tar", "tar", ["tree"])

        assert [e["path"] for e in _core.list_archive_contents("out.tar")] == ["tree/"]
        assert "Skipped tree/sock: sockets are not archived" in caplog.text


class TestArchiveSkipsItsOwnOutput:
    """Test that archiving a tree into itself leaves the archive out."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    @pytest.mark.parametrize("overwrite", [False, True], ids=["new", "overwrite"])
    def test_output_in_the_tree_is_not_archived(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        fmt: str,
        overwrite: bool,
    ) -> None:
        """Test that neither the output nor the temp file beside it is archived."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a" * 10000)
        output = tree / f"out.{fmt}"
        if overwrite:
            _core.create_archive(str(output), fmt, [str(tree)])
            caplog.clear()

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.create_archive(str(output), fmt, [str(tree)], overwrite=2)

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert listed == ["tree/", "tree/a.txt"]
        assert any(
            r.getMessage().endswith("it is the archive being created")
            for r in caplog.records
        )
        assert sorted(p.name for p in tree.iterdir()) == ["a.txt", output.name]

    def test_progress_total_leaves_out_the_old_archive(self, tmp_path: Path) -> None:
        """Test that the progress total counts only what is archived."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a" * 10000)
        output = tree / "out.tar"
        output.write_bytes(bytes(50000))

        totals: list[int] = []
        _core.create_archive(
            str(output),
            "tar",
            [str(tree)],
            overwrite=2,
            progress=lambda done, total: totals.append(total),
        )

        assert totals
        assert set(totals) == {10000}


# A small thread stack, which recursing per directory level overflowed; run in
# a child process, since that is a crash rather than an exception
_ARCHIVE_ON_SMALL_STACK = """
import sys, threading
from compresso import _core
threading.stack_size(128 * 1024)
t = threading.Thread(target=_core.create_archive, args=(sys.argv[1], "tar", [sys.argv[2]]))
t.start()
t.join()
print(len(_core.list_archive_contents(sys.argv[1])))
"""


class TestArchiveWalk:
    """Test the walk over the source tree."""

    def test_deep_tree_needs_no_deep_stack(self, tmp_path: Path) -> None:
        """Test that nesting depth costs heap, not C stack."""
        # Shallow enough that the full path stays under Windows' MAX_PATH
        deepest = tmp_path / "t" / Path(*["d"] * 60)
        deepest.mkdir(parents=True)
        (deepest / "f").write_text("f")

        result = run_child(
            _ARCHIVE_ON_SMALL_STACK, str(tmp_path / "out.tar"), str(tmp_path / "t")
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "62"

    def test_entries_follow_their_directory(self, tmp_path: Path) -> None:
        """Test that each directory's contents come straight after it."""
        for rel in ("t/a/x", "t/a/y/z", "t/b"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(rel)
        output = tmp_path / "out.tar"

        _core.create_archive(str(output), "tar", [str(tmp_path / "t")])

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert sorted(listed) == sorted(
            ["t/", "t/a/", "t/a/x", "t/a/y/", "t/a/y/z", "t/b"]
        )
        for i, path in enumerate(listed):
            if path.endswith("/"):
                inside = [p for p in listed if p.startswith(path) and p != path]
                assert listed[i + 1 : i + 1 + len(inside)] == inside, listed


class TestSizeChange:
    """Test a file whose size changes between the walk and its copy."""

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    @pytest.mark.parametrize(
        ("change", "fill", "message"),
        [
            ("grow", b"x", "file grew while being archived"),
            ("shrink", b"\0", "file shrank by"),
        ],
    )
    def test_warns_and_keeps_the_walked_size(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        fmt: str,
        change: str,
        fill: bytes,
        message: str,
    ) -> None:
        """Test that the entry keeps its walked size, with a warning."""
        first = tmp_path / "a.bin"
        first.write_bytes(b"a" * (4 << 20))
        source = tmp_path / "f.bin"
        source.write_bytes(b"x" * (4 << 20))
        output = tmp_path / f"out.{fmt}"
        changed = False

        def change_source(done: int, total: int) -> None:
            # The first report comes while a.bin is still being read
            nonlocal changed
            if not changed:
                changed = True
                with source.open("r+b") as f:
                    if change == "grow":
                        f.seek(0, os.SEEK_END)
                        f.write(b"y" * (1 << 20))
                    else:
                        f.truncate(0)

        with caplog.at_level(logging.WARNING, logger="compresso"):
            _core.create_archive(
                str(output), fmt, [str(first), str(source)], progress=change_source
            )

        if fmt == "zip":
            with zipfile.ZipFile(output) as zf:
                data = zf.read("f.bin")
        else:
            with tarfile.open(output) as tf:
                f = tf.extractfile("f.bin")
                assert f is not None
                data = f.read()
        assert data == fill * (4 << 20)
        assert f"{source}: {message}" in caplog.text

    @pytest.mark.parametrize("fmt", ["tar", "zip"])
    def test_file_removed_before_its_read(self, tmp_path: Path, fmt: str) -> None:
        """Test that a file gone by its turn names itself, and no output is left."""
        first = tmp_path / "a.bin"
        first.write_bytes(b"a" * (4 << 20))
        source = tmp_path / "f.bin"
        source.write_bytes(b"x")
        output = tmp_path / f"out.{fmt}"

        def remove_source(done: int, total: int) -> None:
            source.unlink(missing_ok=True)

        with pytest.raises(FileNotFoundError) as info:
            _core.create_archive(
                str(output), fmt, [str(first), str(source)], progress=remove_source
            )

        assert info.value.filename == str(source)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.bin"]


class TestTarUtf8Names:
    """Test that tar names reach the archive as UTF-8, as pax requires."""

    def test_names_outside_the_ansi_code_page_round_trip(self, tmp_path: Path) -> None:
        """Test that a name and symlink target outside ASCII and cp1252 survive."""
        name = "日本-ж"
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / name).write_text("x")
        link = tree / f"{name}-sym"
        # Without symlink privilege on Windows, the file name is still checked
        with contextlib.suppress(OSError):
            link.symlink_to(name)
        names = sorted(os.listdir(tree))
        output = tmp_path / "out.tar"

        _core.create_archive(str(output), "tar", [str(tree)])

        listed = [e["path"] for e in _core.list_archive_contents(str(output))]
        assert sorted(listed) == ["tree/"] + [f"tree/{n}" for n in names]
        with tarfile.open(output) as tf:
            if link.is_symlink():
                assert tf.getmember(f"tree/{name}-sym").linkname == name
        _core.extract_archive(str(output), str(tmp_path / "out"), [], allow_symlinks=1)
        assert sorted(os.listdir(tmp_path / "out" / "tree")) == names

    def test_name_the_locale_cant_convert_is_kept(self, tmp_path: Path) -> None:
        """Test that a non-ASCII name under the C locale is archived, as bsdtar does."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "café").write_text("x")
        output = tmp_path / "out.tar"
        # UTF-8 mode without coercion keeps libarchive's locale at plain C
        env = {
            **os.environ,
            "LC_ALL": "C",
            "PYTHONUTF8": "1",
            "PYTHONCOERCECLOCALE": "0",
        }

        result = run_child(ARCHIVE_AND_LIST, str(output), "tar", str(tree), env=env)

        assert result.returncode == 0, result.stderr
        assert orjson.loads(result.stdout) == ["tree/", "tree/café"]


class TestHardlinkCreation:
    """Test that tar stores a file's later names as links to its first."""

    def _round_trip(self, tmp_path: Path, make: Callable[[Path], object]) -> Path:
        """Archive src/a and its hardlink src/b, check the listing, and extract."""
        src = tmp_path / "src"
        src.mkdir()
        make(src / "a")
        os.link(src / "a", src / "b")
        archive = tmp_path / "a.tar"

        _core.create_archive(str(archive), "tar", [str(src)])

        entries = {e["path"]: e for e in _core.list_archive_contents(str(archive))}
        first, later = sorted(["src/a", "src/b"], key=list(entries).index)
        assert entries[later]["type"] == "hardlink"
        assert entries[later]["link_target"] == first
        out = tmp_path / "out"
        _core.extract_archive(str(archive), str(out), [])
        return out / "src"

    def test_file_is_stored_once(self, tmp_path: Path) -> None:
        """Test that the extracted names share an inode."""
        out = self._round_trip(tmp_path, lambda p: p.write_text("x"))

        assert (out / "a").read_text() == "x"
        assert (out / "a").stat().st_ino == (out / "b").stat().st_ino

    @pytest.mark.skipif(sys.platform == "win32", reason="no FIFOs on Windows")
    def test_fifo_is_linked(self, tmp_path: Path) -> None:
        """Test that a link naming a FIFO links it, as bsdtar does."""
        out = self._round_trip(tmp_path, os.mkfifo)

        assert stat.S_ISFIFO((out / "b").lstat().st_mode)
        assert (out / "a").lstat().st_ino == (out / "b").lstat().st_ino

    def test_zip_keeps_copies(self, tmp_path: Path) -> None:
        """Test that zip stores each name in full, as Info-ZIP does."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a").write_text("x")
        os.link(src / "a", src / "b")
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            assert zf.read("src/b") == b"x"


class TestZipModesAndSymlinks:
    """Test that zip keeps Unix modes and symlinks in its external attributes."""

    @pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX modes")
    def test_modes_are_stored(self, tmp_path: Path) -> None:
        """Test that each entry's mode lands in the attributes, typed."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "run").write_bytes(b"x")
        (src / "run").chmod(0o750)
        src.chmod(0o750)
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            modes = {i.filename: i.external_attr >> 16 for i in zf.infolist()}
        assert modes["src/run"] == 0o100750
        assert modes["src/"] == 0o040750

    @pytest.mark.usefixtures("needs_symlinks")
    def test_link_is_stored_as_a_link(self, tmp_path: Path) -> None:
        """Test that a symlink is its target text, marked S_IFLNK."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "link").symlink_to("target")
        archive = tmp_path / "a.zip"

        _core.create_archive(str(archive), "zip", [str(src)])

        with zipfile.ZipFile(archive) as zf:
            info = zf.getinfo("src/link")
            assert stat.S_ISLNK(info.external_attr >> 16)
            assert zf.read(info) == b"target"


class TestOutputsCommitOnlyOnSuccess:
    """Test that outputs go through a temp file, so a failure leaves no trace."""

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz", "zip"])
    def test_failed_archive_creation_keeps_the_existing_file(
        self, sample_text_file: Path, tmp_path: Path, fmt: str
    ) -> None:
        """Test that an archive that fails part way never replaces the one there."""
        existing = tmp_path / f"existing.{fmt}"
        existing.write_bytes(b"keep me")

        with pytest.raises(FileNotFoundError):
            _core.create_archive(
                str(existing),
                fmt,
                [str(sample_text_file), str(tmp_path / "missing")],
                overwrite=2,
            )

        assert existing.read_bytes() == b"keep me"
        assert not list(tmp_path.glob(".compresso-*"))

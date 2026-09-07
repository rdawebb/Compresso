"""Setup script for the Compresso package.

Handles headers and libraries for the `compresso._core` C extension,
resolving them per-platform via `pkg-config`, Homebrew prefix, or environment variables.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from setuptools import Extension, find_packages, setup

# pkg-config module names for the linked libraries that provide a .pc file
# bzip2 and snappy are intentionally absent, as they ship no pkg-config file and
# are covered by the Homebrew-prefix/system-default fallbacks
PKG_CONFIG_MODULES = (
    "libarchive",
    "libzip",
    "zlib",
    "liblzma",
    "libzstd",
    "liblz4",
)


def _run(cmd: list[str]) -> str | None:
    """Run `cmd` and return stripped stdout, or None if it is unavailable/fails.

    Args:
        cmd: The command to run as a list of strings (e.g. `["ls", "-l"]`).

    Returns:
        The stripped stdout of the command, or None if the command is unavailable or fails.
    """
    if shutil.which(cmd[0]) is None:
        return None

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    except (subprocess.CalledProcessError, OSError):
        return None

    return result.stdout.strip()


def _pkg_config_dirs() -> tuple[list[str], list[str]]:
    """Collect include/library dirs reported by pkg-config for the known modules.

    Returns:
        A tuple of `(include_dirs, library_dirs)` lists.
    """
    include_dirs: list[str] = []
    library_dirs: list[str] = []
    for module in PKG_CONFIG_MODULES:
        cflags = _run(["pkg-config", "--cflags-only-I", module])
        if cflags:
            include_dirs += [
                flag[2:] for flag in cflags.split() if flag.startswith("-I")
            ]

        libs = _run(["pkg-config", "--libs-only-L", module])
        if libs:
            library_dirs += [flag[2:] for flag in libs.split() if flag.startswith("-L")]

    return include_dirs, library_dirs


def _homebrew_dirs() -> tuple[list[str], list[str]]:
    """Fallback include/library dirs from the Homebrew prefix (macOS).

    Returns:
        A tuple of `(include_dirs, library_dirs)` lists.
    """
    include_dirs: list[str] = []
    library_dirs: list[str] = []
    prefix = _run(["brew", "--prefix"])
    if prefix:
        include_dirs.append(os.path.join(prefix, "include"))
        library_dirs.append(os.path.join(prefix, "lib"))

    # libarchive is keg-only on Homebrew, so its dirs are not under the main prefix.
    libarchive_prefix = _run(["brew", "--prefix", "libarchive"])
    if libarchive_prefix:
        include_dirs.append(os.path.join(libarchive_prefix, "include"))
        library_dirs.append(os.path.join(libarchive_prefix, "lib"))

    return include_dirs, library_dirs


def _env_dirs() -> tuple[list[str], list[str]]:
    """Explicit override dirs from environment variables.

    Returns:
        A tuple of `(include_dirs, library_dirs)` lists.
    """
    include_dirs = os.environ.get("COMPRESSO_EXTRA_INCLUDE_DIRS", "").split(os.pathsep)
    library_dirs = os.environ.get("COMPRESSO_EXTRA_LIBRARY_DIRS", "").split(os.pathsep)

    return [d for d in include_dirs if d], [d for d in library_dirs if d]


def _dedupe(dirs: list[str]) -> list[str]:
    """Drop duplicate directories while preserving order.

    Args:
        dirs: The list of directories to dedupe.

    Returns:
        A list of deduped directories.
    """
    seen: set[str] = set()
    result: list[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            result.append(d)

    return result


# Windows import-library basenames, in preference order per library. vcpkg's
# spellings do not match the Unix `-l` names and have changed across vcpkg
# revisions, so the exact name is probed for on disk rather than hard-coded.
WINDOWS_LIBRARY_CANDIDATES = (
    ("zlib", "zlibstatic", "zlib1", "z"),
    ("bz2", "libbz2", "bzip2"),
    ("lzma", "liblzma"),
    ("zstd", "libzstd", "zstd_static"),
    ("lz4", "liblz4"),
    ("snappy", "libsnappy"),
    ("zip", "libzip"),
    ("archive", "libarchive", "archive_static"),
)


def _windows_libraries(library_dirs: list[str]) -> list[str]:
    """Pick each library's import-lib basename by probing `library_dirs`.

    Args:
        library_dirs: Directories to search for a matching `<name>.lib`.

    Returns:
        One basename per entry in `WINDOWS_LIBRARY_CANDIDATES`, preserving its
        order. Falls back to the first candidate when none is found on disk, so
        the linker reports a recognisable name instead of this silently
        substituting a wrong one.
    """
    available = {
        entry[:-4]
        for d in library_dirs
        if os.path.isdir(d)
        for entry in os.listdir(d)
        if entry.lower().endswith(".lib")
    }

    resolved: list[str] = []
    missing: list[tuple[str, ...]] = []
    for candidates in WINDOWS_LIBRARY_CANDIDATES:
        found = next((name for name in candidates if name in available), None)
        if found is None:
            found = candidates[0]
            missing.append(candidates)

        resolved.append(found)

    # A miss means the linker is about to fail, so say enough to diagnose it in
    # one CI run: whether the directories were empty or just spelled otherwise
    if missing:
        print(f"setup.py: searched {library_dirs}")
        print(f"setup.py: found .lib files {sorted(available)}")
        print(f"setup.py: no match for {missing}, falling back to first candidate")

    return resolved


def resolve_dirs() -> tuple[list[str], list[str]]:
    """Resolve include/library dirs: pkg-config, then Homebrew, then env overrides.

    Returns:
        A tuple of `(include_dirs, library_dirs)` lists.
    """
    include_dirs: list[str] = []
    library_dirs: list[str] = []
    for inc, lib in (_pkg_config_dirs(), _homebrew_dirs(), _env_dirs()):
        include_dirs += inc
        library_dirs += lib

    return _dedupe(include_dirs), _dedupe(library_dirs)


include_dirs, library_dirs = resolve_dirs()

# Per-platform link names
if sys.platform == "win32":
    libraries = _windows_libraries(library_dirs)
    extra_compile_args = ["/O2"]
else:
    libraries = ["z", "bz2", "lzma", "zstd", "lz4", "snappy", "zip", "archive"]
    extra_compile_args = ["-O2", "-std=gnu11"]

setup(
    name="compresso",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            name="compresso._core",
            sources=[
                "src/compresso/csrc/_core.c",
                "src/compresso/csrc/common.c",
                "src/compresso/csrc/compress.c",
                "src/compresso/csrc/format.c",
                "src/compresso/csrc/registry.c",
                "src/compresso/csrc/strategy.c",
                "src/compresso/csrc/archives.c",
                "src/compresso/csrc/validate.c",
                "src/compresso/csrc/fsutil.c",
                # Compression algorithms
                "src/compresso/csrc/compression/py_zlib.c",
                "src/compresso/csrc/compression/py_bzip2.c",
                "src/compresso/csrc/compression/py_lzma.c",
                "src/compresso/csrc/compression/py_zstd.c",
                "src/compresso/csrc/compression/py_lz4.c",
                "src/compresso/csrc/compression/py_snappy.c",
                # Archive backends
                "src/compresso/csrc/archives/tar.c",
                "src/compresso/csrc/archives/zip.c",
                # Standalone formats
                "src/compresso/csrc/standalone/gzip.c",
                "src/compresso/csrc/standalone/bzip2.c",
                "src/compresso/csrc/standalone/xz.c",
                "src/compresso/csrc/standalone/zstd.c",
                "src/compresso/csrc/standalone/lz4.c",
                "src/compresso/csrc/standalone/registry.c",
            ],
            include_dirs=include_dirs,
            library_dirs=library_dirs,
            libraries=libraries,
            extra_compile_args=extra_compile_args,
        )
    ],
    python_requires=">=3.10",
)

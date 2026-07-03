"""Setup script for the Compresso package."""

from setuptools import Extension, find_packages, setup

setup(
    name="compresso",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            name="compresso._core",
            sources=[
                "src/compresso/csrc/_core.c",
                "src/compresso/csrc/compress.c",
                "src/compresso/csrc/format.c",
                "src/compresso/csrc/registry.c",
                "src/compresso/csrc/strategy.c",
                "src/compresso/csrc/archives.c",
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
            include_dirs=[
                "/usr/local/opt/libarchive/include",
                "/usr/local/include",
            ],
            library_dirs=[
                "/usr/local/opt/libarchive/lib",
            ],
            libraries=["z", "bz2", "lzma", "zstd", "lz4", "snappy", "zip", "archive"],
        )
    ],
    python_requires=">=3.9",
)

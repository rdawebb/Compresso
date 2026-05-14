"""Setup script for the Compresso package."""

from setuptools import setup, Extension, find_packages

setup(
    name="compresso",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            name="compresso._core",
            sources=[
                "src/compresso/csrc/_core.c",
                "src/compresso/csrc/router.c",
                "src/compresso/csrc/format_detection.c",
                # Compression algorithms
                "src/compresso/csrc/compression/py_zlib.c",
                "src/compresso/csrc/compression/py_bzip2.c",
                "src/compresso/csrc/compression/py_lzma.c",
                "src/compresso/csrc/compression/py_zstd.c",
                "src/compresso/csrc/compression/py_lz4.c",
                "src/compresso/csrc/compression/py_snappy.c",
            ],
            include_dirs=[
                "/usr/local/opt/libarchive/include",
                "/usr/local/include",
            ],
            library_dirs=[
                "/usr/local/opt/libarchive/lib",
            ],
            libraries=["z", "bz2", "lzma", "zstd", "lz4", "snappy", "archive"],
        )
    ],
    python_requires=">=3.9",
)

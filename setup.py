"""Setup script for the Compresso package."""

from setuptools import setup, Extension, find_packages

setup(
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            "compresso._core",
            sources=[
                "src/compresso/csrc/_core.c",
                "src/compresso/csrc/router.c",
                "src/compresso/csrc/py_zlib.c",
                "src/compresso/csrc/py_bzip2.c",
                "src/compresso/csrc/py_lzma.c",
                "src/compresso/csrc/py_zstd.c",
                "src/compresso/csrc/py_lz4.c",
                "src/compresso/csrc/py_snappy.c",
            ],
            libraries=["z", "bz2", "lzma", "zstd", "lz4", "snappy"],
        )
    ],
    python_requires=">=3.11",
)
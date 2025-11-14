

from setuptools import setup, Extension, find_packages

setup(
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            "compressor._core",
            sources=[
                "src/compressor/_core.c",
                "src/compressor/router.c",
                "src/compressor/py_zlib.c",
                "src/compressor/py_bzip2.c",
                "src/compressor/py_lzma.c",
                "src/compressor/py_zstd.c",
                "src/compressor/py_lz4.c",
                "src/compressor/py_snappy.c",
            ],
            libraries=["z", "bz2", "lzma", "zstd", "lz4", "snappy"],
        )
    ],
    python_requires=">=3.11",
)
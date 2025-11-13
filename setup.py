

from setuptools import setup, Extension, find_packages

setup(
    name="Compresso",
    version="0.1.0",
    author="Rob Webb",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=[
        Extension(
            "compresser._core",
            sources=[
                "src/compresser/_core.c",
                "src/compresser/router.c",
                "src/compresser/py_zlib.c",
                "src/compresser/py_bzip2.c",
                "src/compresser/py_lzma.c"
            ],
            libraries=["z", "bz2", "lzma"]
        )
    ],
    python_requires=">=3.11",
)
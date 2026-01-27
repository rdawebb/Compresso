"""Cache cleaning script"""

import shutil
from pathlib import Path


def remove_dirs(root: Path, dir_names: list[str]) -> None:
    """Remove directories with specified names under the root path

    Args:
        root: Root directory to start searching from
        dir_names: List of directory names to remove
    """
    for dir in dir_names:
        for path in root.rglob(dir):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


def remove_files(root: Path, extensions: list[str]) -> None:
    """Remove files with specified extensions under the root path

    Args:
        root: Root directory to start searching from
        extensions: List of file extensions to remove
    """
    for ext in extensions:
        for file in root.rglob(f"*{ext}"):
            if file.is_file():
                try:
                    file.unlink()
                except Exception:
                    pass


def clean() -> None:
    """Clean up temporary files and directories."""
    root = Path(".")
    remove_dirs(
        root,
        [
            "__pycache__",
            ".ruff_cache",
            ".pytest_cache",
            "compresso.egg-info",
            "build",
            "dist",
            "htmlcov",
        ],
    )
    remove_files(root, [".pyc", ".so", ".pyd", ".dll"])
    print("Cache cleaned!")


if __name__ == "__main__":
    clean()

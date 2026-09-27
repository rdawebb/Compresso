# libarchive is keg-only on Homebrew, so pkg-config cannot find it unaided
export PKG_CONFIG_PATH := `brew --prefix libarchive 2>/dev/null | sed 's|$|/lib/pkgconfig|'` + ":" + env("PKG_CONFIG_PATH", "")

# Install in editable mode, rebuilding the extension from scratch
install:
    uv sync --all-extras --reinstall-package compresso

# Install development dependencies
install-dev:
    uv sync --all-extras

# Lint Python code
lint:
    uv run ruff check --fix

# Format Python code
format:
    uv run ruff format

# Type check Python code
type:
    uv run ty check

# Check code quality
check: lint format type

# Run all Python tests
test:
    uv run pytest -v

# Run Python tests with coverage
test-cov:
    uv run pytest --cov --cov-report=html --cov-report=term

# Run all C tests
test-c:
    cd tests/c && rake test:all

# Run all pre-commit hooks
pre:
    uv run prek run --all-files

# Link the editable build's compile_commands.json to the root, where clangd looks
compdb:
    ln -sf "$(ls -td build/cp*/ | head -1)compile_commands.json" compile_commands.json

# Clean up temporary files
clean:
    uv run python scripts/clean.py

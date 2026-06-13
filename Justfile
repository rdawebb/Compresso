# Install in editable mode
install:
    uv pip install -e .

# Install development dependencies
install-dev:
    uv sync --all-extras

# Lint Python code
lint:
    uv run ruff check --fix src/ tests/

# Format Python code
format:
    uv run ruff format src/ tests/

# Type check Python code
type:
    uv run ty check src/ tests/

# Check code quality
check: lint format type

# Run all Python tests
test:
    uv run pytest tests/ -v

# Run Python tests with coverage
test-cov:
    uv run pytest tests/ --cov=src --cov-report=html --cov-report=term

# Run all C tests
test-c:
    cd tests/c && rake test:all

# Run all pre-commit hooks
pre:
    uv run prek run --all-files

# Clean up temporary files
clean:
    uv run python scripts/clean.py

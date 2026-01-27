"""Tests for the CLI module."""

import pytest

from compresso.cli import format_size, format_time


class TestFormatters:
    """Test utility formatting functions."""

    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, "0.00 B"),
            (500, "500.00 B"),
            (1024, "1.00 KB"),
            (1536, "1.50 KB"),
            (1048576, "1.00 MB"),
            (1073741824, "1.00 GB"),
            (1099511627776, "1.00 TB"),
        ],
    )
    def test_format_size(self, size: int, expected: str):
        """Test size formatting with various values."""
        result = format_size(size)
        assert result == expected

    @pytest.mark.parametrize(
        "seconds,expected_pattern",
        [
            (0.0005, "ms"),  # Less than 1ms
            (0.5, "ms"),  # 500ms
            (1.0, "1.00s"),
            (5.5, "5.50s"),
            (59.9, "59.90s"),
            (60.0, "1m"),  # 1 minute
            (90.5, "1m 30"),  # 1m 30s
            (125.0, "2m"),  # 2m 5s
        ],
    )
    def test_format_time(self, seconds: float, expected_pattern: str):
        """Test time formatting with various values."""
        result = format_time(seconds)
        assert expected_pattern in result


class TestCLIModule:
    """Test the CLI module structure."""

    def test_cli_app_exists(self):
        """Test that the CLI app exists."""
        from compresso.cli import app

        assert app is not None
        assert hasattr(app, "__call__")

    def test_compress_command_exists(self):
        """Test that compress command is defined."""
        from compresso import cli

        assert hasattr(cli, "compress")
        assert callable(cli.compress)

    def test_decompress_command_exists(self):
        """Test that decompress command is defined."""
        from compresso import cli

        assert hasattr(cli, "decompress")
        assert callable(cli.decompress)

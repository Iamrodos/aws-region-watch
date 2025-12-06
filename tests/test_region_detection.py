"""Tests for AWS region detection logic."""

import pytest
from pathlib import Path

from aws_region_watch import get_default_region


class TestGetDefaultRegion:
    """Tests for get_default_region function."""

    def test_returns_aws_region_env_var(self, monkeypatch):
        """AWS_REGION takes highest precedence."""
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        assert get_default_region() == "us-east-1"

    def test_returns_aws_default_region_env_var(self, monkeypatch):
        """AWS_DEFAULT_REGION is used when AWS_REGION is not set."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        assert get_default_region() == "eu-west-1"

    def test_reads_from_aws_config_file(self, monkeypatch, tmp_path):
        """Falls back to ~/.aws/config when env vars not set."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

        # Create mock AWS config
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        config_file = aws_dir / "config"
        config_file.write_text("[default]\nregion = ap-southeast-2\n")

        # Patch Path.home() to return our temp directory
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert get_default_region() == "ap-southeast-2"

    def test_returns_none_when_no_region_configured(self, monkeypatch, tmp_path):
        """Returns None when no region is configured anywhere."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert get_default_region() is None

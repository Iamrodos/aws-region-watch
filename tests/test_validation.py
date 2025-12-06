"""Tests for input validation functions."""

import pytest

from aws_region_watch import validate_region_name


class TestValidateRegionName:
    """Tests for region name validation (security)."""

    def test_valid_region_names(self):
        """Standard AWS region names should pass validation."""
        valid_regions = [
            "us-east-1",
            "us-west-2",
            "ap-southeast-2",
            "eu-central-1",
            "sa-east-1",
        ]
        for region in valid_regions:
            assert validate_region_name(region) == region

    def test_rejects_path_traversal_with_double_dots(self):
        """Reject path traversal attempts with '..'."""
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region_name("../etc/passwd")

    def test_rejects_forward_slashes(self):
        """Reject region names containing forward slashes."""
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region_name("us-east-1/../../etc")

    def test_rejects_backslashes(self):
        """Reject region names containing backslashes."""
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region_name("us-east-1\\..\\etc")

    def test_rejects_absolute_paths(self):
        """Reject absolute path attempts."""
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region_name("/etc/passwd")

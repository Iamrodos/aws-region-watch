"""Pytest configuration and fixtures for aws-region-watch tests."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for state files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def sample_regions_response() -> dict:
    """Sample response from aws___list_regions API call."""
    return {
        "content": {
            "result": [
                {"region_id": "us-east-1", "region_long_name": "US East (N. Virginia)"},
                {"region_id": "us-west-2", "region_long_name": "US West (Oregon)"},
                {"region_id": "ap-southeast-2", "region_long_name": "Asia Pacific (Sydney)"},
            ]
        }
    }


@pytest.fixture
def sample_products_response() -> dict:
    """Sample response from aws___get_regional_availability for products."""
    return {
        "content": {
            "result": {
                "products": {
                    "Amazon S3": "isAvailableIn",
                    "AWS Lambda": "isAvailableIn",
                    "Amazon Bedrock": "isPlannedIn",
                }
            }
        }
    }


@pytest.fixture
def sample_state() -> dict:
    """Sample saved state for comparison tests."""
    return {
        "_schema_version": 1,
        "region": "ap-southeast-2",
        "product": {
            "Amazon S3": "isAvailableIn",
            "AWS Lambda": "isAvailableIn",
        },
    }


def create_mcp_response(content: dict) -> dict:
    """Helper to create a properly formatted MCP API response."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {"text": json.dumps(content)}
            ]
        }
    }

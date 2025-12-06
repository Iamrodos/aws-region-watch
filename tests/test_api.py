"""Tests for API interaction with AWS Knowledge MCP."""

import json
import pytest
import httpx

from aws_region_watch import (
    call_mcp_tool,
    fetch_all_regions,
    APIError,
    MCP_API_URL,
)

# Import helper from conftest (pytest makes conftest available but not as a module)
from tests.conftest import create_mcp_response


class TestCallMcpTool:
    """Tests for the low-level MCP API call function."""

    def test_successful_api_call(self, httpx_mock):
        """Successful API call returns parsed content."""
        response_content = {"content": {"result": [{"id": "test"}]}}
        httpx_mock.add_response(
            url=MCP_API_URL,
            json=create_mcp_response(response_content),
        )

        result = call_mcp_tool("aws___list_regions", {})
        assert result == response_content

    def test_sends_correct_jsonrpc_payload(self, httpx_mock):
        """Verify the JSON-RPC payload structure."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_response(json=create_mcp_response(response_content))

        call_mcp_tool("aws___list_regions", {"region": "us-east-1"})

        request = httpx_mock.get_request()
        payload = json.loads(request.content)

        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "aws___list_regions"
        assert payload["params"]["arguments"] == {"region": "us-east-1"}

    def test_raises_api_error_on_client_error(self, httpx_mock):
        """Client errors (4xx except 429) raise APIError immediately."""
        httpx_mock.add_response(status_code=400)

        with pytest.raises(APIError, match="API request failed: 400"):
            call_mcp_tool("aws___list_regions", {})

    def test_raises_api_error_on_mcp_error_response(self, httpx_mock):
        """MCP error in response raises APIError."""
        httpx_mock.add_response(
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "Invalid request"},
            }
        )

        with pytest.raises(APIError, match="MCP error"):
            call_mcp_tool("aws___list_regions", {})

    def test_raises_api_error_on_invalid_json_response(self, httpx_mock):
        """Invalid JSON response raises APIError."""
        httpx_mock.add_response(content=b"not json")

        with pytest.raises(APIError, match="Invalid JSON"):
            call_mcp_tool("aws___list_regions", {})


class TestFetchAllRegions:
    """Tests for fetching all AWS regions."""

    def test_returns_region_dict(self, httpx_mock, sample_regions_response):
        """Returns dict mapping region_id to region_long_name."""
        httpx_mock.add_response(
            json=create_mcp_response(sample_regions_response),
        )

        regions = fetch_all_regions()

        assert regions == {
            "us-east-1": "US East (N. Virginia)",
            "us-west-2": "US West (Oregon)",
            "ap-southeast-2": "Asia Pacific (Sydney)",
        }

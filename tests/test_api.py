"""Tests for API interaction with AWS Knowledge MCP."""

import json
from unittest.mock import patch

import httpx
import pytest

from aws_region_watch import (
    call_mcp_tool,
    fetch_all_regions,
    fetch_region_resources,
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


class TestCallMcpToolRetry:
    """Tests for retry logic in call_mcp_tool."""

    @patch("aws_region_watch.time.sleep")
    def test_retries_on_rate_limit(self, mock_sleep, httpx_mock):
        """Retries on 429 rate limit response."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(json=create_mcp_response(response_content))

        result = call_mcp_tool("aws___list_regions", {})

        assert result == response_content
        assert len(httpx_mock.get_requests()) == 2
        mock_sleep.assert_called_once()

    @patch("aws_region_watch.time.sleep")
    def test_retries_on_server_error(self, mock_sleep, httpx_mock):
        """Retries on 5xx server errors."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_response(status_code=503)
        httpx_mock.add_response(json=create_mcp_response(response_content))

        result = call_mcp_tool("aws___list_regions", {})

        assert result == response_content
        assert len(httpx_mock.get_requests()) == 2

    @patch("aws_region_watch.time.sleep")
    def test_retries_on_timeout(self, mock_sleep, httpx_mock):
        """Retries on timeout exceptions."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_exception(httpx.TimeoutException("timeout"))
        httpx_mock.add_response(json=create_mcp_response(response_content))

        result = call_mcp_tool("aws___list_regions", {})

        assert result == response_content
        assert len(httpx_mock.get_requests()) == 2

    @patch("aws_region_watch.time.sleep")
    def test_retries_on_network_error(self, mock_sleep, httpx_mock):
        """Retries on network errors."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_exception(httpx.ConnectError("connection failed"))
        httpx_mock.add_response(json=create_mcp_response(response_content))

        result = call_mcp_tool("aws___list_regions", {})

        assert result == response_content
        assert len(httpx_mock.get_requests()) == 2

    @patch("aws_region_watch.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, httpx_mock):
        """Raises APIError after exhausting retries."""
        # MAX_RETRIES is 6 (from RETRY_BACKOFF_SECONDS = [1, 2, 4, 8, 16, 30])
        for _ in range(6):
            httpx_mock.add_response(status_code=503)

        with pytest.raises(APIError, match="after 6 attempts"):
            call_mcp_tool("aws___list_regions", {})

        assert len(httpx_mock.get_requests()) == 6

    @patch("aws_region_watch.time.sleep")
    def test_uses_retry_after_header(self, mock_sleep, httpx_mock):
        """Uses Retry-After header value when present."""
        response_content = {"content": {"result": []}}
        httpx_mock.add_response(status_code=429, headers={"Retry-After": "5"})
        httpx_mock.add_response(json=create_mcp_response(response_content))

        call_mcp_tool("aws___list_regions", {})

        mock_sleep.assert_called_with(5)


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


class TestFetchRegionResources:
    """Tests for fetching regional resources with pagination."""

    def test_single_page_response(self, httpx_mock):
        """Handles single page of results."""
        response = {
            "content": {
                "result": {
                    "products": {
                        "Amazon S3": "isAvailableIn",
                        "AWS Lambda": "isAvailableIn",
                    }
                }
            }
        }
        httpx_mock.add_response(json=create_mcp_response(response))

        result = fetch_region_resources("us-east-1", "product")

        assert result == {
            "Amazon S3": "isAvailableIn",
            "AWS Lambda": "isAvailableIn",
        }
        assert len(httpx_mock.get_requests()) == 1

    def test_multi_page_response(self, httpx_mock):
        """Handles paginated results with next_token."""
        page1 = {
            "content": {
                "result": {
                    "products": {"Amazon S3": "isAvailableIn"},
                    "next_token": "page2",
                }
            }
        }
        page2 = {
            "content": {
                "result": {
                    "products": {"AWS Lambda": "isAvailableIn"},
                }
            }
        }
        httpx_mock.add_response(json=create_mcp_response(page1))
        httpx_mock.add_response(json=create_mcp_response(page2))

        result = fetch_region_resources("us-east-1", "product")

        assert result == {
            "Amazon S3": "isAvailableIn",
            "AWS Lambda": "isAvailableIn",
        }
        assert len(httpx_mock.get_requests()) == 2

        # Verify second request includes next_token
        second_request = httpx_mock.get_requests()[1]
        payload = json.loads(second_request.content)
        assert payload["params"]["arguments"]["next_token"] == "page2"

    def test_uses_correct_key_for_api_type(self, httpx_mock):
        """Uses 'service_apis' key for api resource type."""
        response = {
            "content": {
                "result": {
                    "service_apis": {
                        "S3+PutObject": "isAvailableIn",
                    }
                }
            }
        }
        httpx_mock.add_response(json=create_mcp_response(response))

        result = fetch_region_resources("us-east-1", "api")

        assert result == {"S3+PutObject": "isAvailableIn"}


class TestAPIResponseValidation:
    """Tests for API response structure validation.

    These tests verify that unexpected API response structures fail fast
    with clear error messages, rather than silently returning empty data.
    """

    def test_fetch_regions_missing_content_key(self, httpx_mock):
        """Raises APIError if 'content' key is missing from response."""
        response = {"result": []}  # Missing 'content' wrapper
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing 'content' key"):
            fetch_all_regions()

    def test_fetch_regions_missing_result_key(self, httpx_mock):
        """Raises APIError if 'result' key is missing from content."""
        response = {"content": {"data": []}}  # 'result' renamed to 'data'
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing 'content.result' key"):
            fetch_all_regions()

    def test_fetch_regions_result_not_a_list(self, httpx_mock):
        """Raises APIError if 'result' is not a list."""
        response = {"content": {"result": {"regions": []}}}  # result is dict, not list
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="is not a list"):
            fetch_all_regions()

    def test_fetch_regions_missing_region_fields(self, httpx_mock):
        """Raises APIError if region objects are missing required fields."""
        response = {"content": {"result": [{"id": "us-east-1", "name": "US East"}]}}  # Wrong field names
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing required fields"):
            fetch_all_regions()

    def test_fetch_resources_missing_content_key(self, httpx_mock):
        """Raises APIError if 'content' key is missing from resource response."""
        response = {"result": {"products": {}}}  # Missing 'content' wrapper
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing 'content' key"):
            fetch_region_resources("us-east-1", "product")

    def test_fetch_resources_missing_result_key(self, httpx_mock):
        """Raises APIError if 'result' key is missing from content."""
        response = {"content": {"data": {"products": {}}}}  # 'result' renamed to 'data'
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing 'content.result' key"):
            fetch_region_resources("us-east-1", "product")

    def test_fetch_resources_missing_products_key(self, httpx_mock):
        """Raises APIError if expected resource key is missing."""
        response = {"content": {"result": {"services": {}}}}  # 'products' renamed to 'services'
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="missing 'products' key"):
            fetch_region_resources("us-east-1", "product")

    def test_fetch_resources_products_not_a_dict(self, httpx_mock):
        """Raises APIError if products is not a dict."""
        response = {"content": {"result": {"products": []}}}  # products is list, not dict
        httpx_mock.add_response(json=create_mcp_response(response))

        with pytest.raises(APIError, match="is not a dict"):
            fetch_region_resources("us-east-1", "product")

#!/usr/bin/env python3
"""
AWS Region Watch - Monitor AWS service/feature availability in regions.

Compares current availability against saved state and reports new capabilities.
"""

import argparse
import configparser
import json
import os
import sys
import time
from importlib.metadata import version as get_version
import httpx
from pathlib import Path
from datetime import datetime

__version__ = get_version("aws-region-watch")

# AWS Knowledge MCP API endpoint
MCP_API_URL = "https://knowledge-mcp.global.api.aws"

# Defaults
DEFAULT_STATE_DIR = Path("state")
DEFAULT_TYPES = ["region", "product"]
VALID_TYPES = {"region", "product", "api"}

# State file schema version - increment when structure changes
STATE_SCHEMA_VERSION = 1

# HTTP client (initialized in main for connection reuse)
_http_client: httpx.Client | None = None

# Exit codes
EXIT_SUCCESS = 0
EXIT_CHANGES = 1
EXIT_ERROR = 2

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [1, 2, 4]  # Exponential backoff

# Friendly status names
STATUS_LABELS = {
    "isAvailableIn": "Available",
    "isPlannedIn": "Planned",
    "isBeingPlannedIn": "Being Planned",
    "isNotExpandingIn": "Not Expanding",
}


# ---------------------------------------------------------------------------
# AWS region detection
# ---------------------------------------------------------------------------

def get_default_region() -> str | None:
    """
    Detect default AWS region using AWS CLI precedence:
    1. AWS_REGION environment variable
    2. AWS_DEFAULT_REGION environment variable
    3. ~/.aws/config file (default profile)

    Returns None if no region configured.
    """
    # Check environment variables first
    if region := os.environ.get("AWS_REGION"):
        return region
    if region := os.environ.get("AWS_DEFAULT_REGION"):
        return region

    # Check AWS config file
    config_path = Path.home() / ".aws" / "config"
    if config_path.exists():
        config = configparser.ConfigParser()
        config.read(config_path)
        if config.has_option("default", "region"):
            return config.get("default", "region")

    return None


# ---------------------------------------------------------------------------
# Logging helpers - progress/debug to stderr, report to stdout
# ---------------------------------------------------------------------------

class Logger:
    """Simple logger that respects quiet/verbose flags."""

    def __init__(self, quiet: bool = False, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose

    def progress(self, msg: str) -> None:
        """Progress messages (stderr) - suppressed by --quiet."""
        if not self.quiet:
            print(msg, file=sys.stderr)

    def detail(self, msg: str) -> None:
        """Verbose details (stderr) - only shown with --verbose."""
        if self.verbose:
            print(f"  [verbose] {msg}", file=sys.stderr)

    def warn(self, msg: str) -> None:
        """Warnings (stderr) - always shown."""
        print(f"Warning: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        """Errors (stderr) - always shown."""
        print(f"Error: {msg}", file=sys.stderr)


# Global logger instance, set in main()
log = Logger()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Raised when API call fails after retries."""
    pass


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------

# Cache for region names
_region_names: dict[str, str] = {}


def get_region_names() -> dict[str, str]:
    """
    Fetch region ID to long name mapping from API.
    Results are cached for the session.
    """
    global _region_names
    if _region_names:
        return _region_names

    log.detail("Fetching region names...")
    data = call_mcp_tool("aws___list_regions", {})
    regions = data.get("content", {}).get("result", [])
    _region_names = {r["region_id"]: r["region_long_name"] for r in regions}
    return _region_names


def get_region_display_name(region_id: str) -> str:
    """Get display name for a region, e.g. 'ap-southeast-2 (Asia Pacific - Sydney)'"""
    names = get_region_names()
    if region_id in names:
        return f"{region_id} - {names[region_id]}"
    return region_id


def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """
    Call an MCP tool via JSON-RPC with retry logic.

    Retries on:
    - Network errors (connection, timeout)
    - Rate limiting (429)
    - Server errors (500, 502, 503, 504)

    Raises APIError after all retries exhausted.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    log.detail(f"API call: {tool_name} with {arguments}")

    def get_backoff(attempt: int) -> int:
        """Get backoff time for attempt, safely handling index bounds."""
        return RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]

    def parse_retry_after(header_value: str | None, default: int) -> int:
        """Parse Retry-After header, handling both integer and invalid values."""
        if header_value is None:
            return default
        try:
            return int(header_value)
        except ValueError:
            return default

    # Use module-level client if available (connection reuse), else create one-off
    client = _http_client or httpx

    last_error = None
    for attempt in range(MAX_RETRIES):
        backoff = get_backoff(attempt)
        try:
            response = client.post(MCP_API_URL, json=payload, timeout=60)

            # Check for rate limiting or server errors (retry these)
            if response.status_code == 429:
                retry_after = parse_retry_after(response.headers.get("Retry-After"), backoff)
                log.warn(f"Rate limited. Retrying in {retry_after}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(retry_after)
                continue

            if response.status_code >= 500:
                log.warn(f"Server error {response.status_code}. Retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(backoff)
                continue

            # Client errors (400-499 except 429) - don't retry
            if response.status_code >= 400:
                raise APIError(f"API request failed: {response.status_code} {response.reason_phrase}")

            try:
                result = response.json()
            except json.JSONDecodeError as e:
                raise APIError(f"Invalid JSON response from API: {e}")

            if "error" in result:
                raise APIError(f"MCP error: {result['error']}")

            # Parse the nested JSON response
            try:
                content = result["result"]["content"][0]["text"]
            except (KeyError, IndexError, TypeError) as e:
                raise APIError(f"Unexpected API response structure: {e}")

            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                raise APIError(f"Invalid JSON in API response content: {e}")

        except httpx.TimeoutException as e:
            last_error = e
            log.warn(f"Request timeout. Retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(backoff)

        except httpx.NetworkError as e:
            last_error = e
            log.warn(f"Network error: {e}. Retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(backoff)

    # All retries exhausted
    raise APIError(f"API request failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_all_regions() -> dict[str, str]:
    """
    Fetch all AWS regions.

    Returns dict of {region_id: region_long_name}
    """
    data = call_mcp_tool("aws___list_regions", {})
    regions = data.get("content", {}).get("result", [])
    return {r["region_id"]: r["region_long_name"] for r in regions}


def fetch_region_resources(region: str, resource_type: str) -> dict[str, str]:
    """
    Fetch all resources of a given type for a region.

    Args:
        region: AWS region code
        resource_type: 'product' or 'api'

    Returns dict of {resource_name: availability_status}
    """
    MAX_PAGES = 100  # Safety limit to prevent infinite loops

    resources = {}
    next_token = None

    # API response uses different keys for different types
    result_key = "products" if resource_type == "product" else "service_apis"

    for page in range(1, MAX_PAGES + 1):
        args = {
            "region": region,
            "resource_type": resource_type,
        }
        if next_token:
            args["next_token"] = next_token

        data = call_mcp_tool("aws___get_regional_availability", args)

        # Extract resources from response
        result = data.get("content", {}).get("result", {})
        page_resources = result.get(result_key, {})
        for name, status in page_resources.items():
            resources[name] = status

        log.detail(f"Page {page}: fetched {len(page_resources)} {resource_type}s")

        # Check for pagination
        next_token = result.get("next_token")
        if not next_token:
            break
    else:
        raise APIError(f"Exceeded maximum pages ({MAX_PAGES}) fetching {resource_type}s for {region}")

    return resources


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def validate_region_name(region: str) -> str:
    """
    Validate region name to prevent path traversal attacks.

    Raises ValueError if region contains unsafe characters.
    """
    # Check for path traversal attempts
    if ".." in region or "/" in region or "\\" in region:
        raise ValueError(f"Invalid region name: {region}")
    # Ensure the region name is just a filename component
    if Path(region).name != region:
        raise ValueError(f"Invalid region name: {region}")
    return region


def get_state_file(state_dir: Path, region: str) -> Path:
    """Get the state file path for a region."""
    safe_region = validate_region_name(region)
    return state_dir / f"{safe_region}.json"


def get_global_state_file(state_dir: Path) -> Path:
    """Get the global state file path (for regions list)."""
    return state_dir / "regions.json"


def load_region_state(state_dir: Path, region: str) -> dict:
    """Load previous state for a region."""
    state_file = get_state_file(state_dir, region)
    if state_file.exists():
        log.detail(f"Loading state from {state_file}")
        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError as e:
            log.warn(f"Corrupted state file {state_file}: {e}")
            log.warn("Starting fresh for this region")
            return {}
        version = state.get("_schema_version", 0)
        if version != STATE_SCHEMA_VERSION:
            raise NotImplementedError(
                f"State file {state_file} has schema version {version}, "
                f"but this version only supports {STATE_SCHEMA_VERSION}. "
                f"Delete the state file to start fresh."
            )
        return state
    log.detail(f"No existing state file at {state_file}")
    return {}


def load_global_state(state_dir: Path) -> dict:
    """Load global state (regions list)."""
    state_file = get_global_state_file(state_dir)
    if state_file.exists():
        log.detail(f"Loading global state from {state_file}")
        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError as e:
            log.warn(f"Corrupted global state file {state_file}: {e}")
            log.warn("Starting fresh for global state")
            return {}
        version = state.get("_schema_version", 0)
        if version != STATE_SCHEMA_VERSION:
            raise NotImplementedError(
                f"Global state file {state_file} has schema version {version}, "
                f"but this version only supports {STATE_SCHEMA_VERSION}. "
                f"Delete the state file to start fresh."
            )
        return state
    log.detail(f"No existing global state file at {state_file}")
    return {}


def save_region_state(state: dict, state_dir: Path, region: str) -> None:
    """Save state for a region using atomic write."""
    state["_schema_version"] = STATE_SCHEMA_VERSION
    state["_last_updated"] = datetime.now().isoformat()

    # Ensure state directory exists
    state_dir.mkdir(parents=True, exist_ok=True)

    state_file = get_state_file(state_dir, region)
    tmp_file = state_file.with_suffix(".json.tmp")

    # Write to temp file, then atomic rename
    try:
        tmp_file.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp_file.rename(state_file)
    except Exception:
        # Clean up temp file on failure
        if tmp_file.exists():
            tmp_file.unlink()
        raise

    log.detail(f"State saved to {state_file}")


def save_global_state(state: dict, state_dir: Path) -> None:
    """Save global state using atomic write."""
    state["_schema_version"] = STATE_SCHEMA_VERSION
    state["_last_updated"] = datetime.now().isoformat()

    # Ensure state directory exists
    state_dir.mkdir(parents=True, exist_ok=True)

    state_file = get_global_state_file(state_dir)
    tmp_file = state_file.with_suffix(".json.tmp")

    # Write to temp file, then atomic rename
    try:
        tmp_file.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp_file.rename(state_file)
    except Exception:
        # Clean up temp file on failure
        if tmp_file.exists():
            tmp_file.unlink()
        raise

    log.detail(f"Global state saved to {state_file}")


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

def compare_states(old: dict[str, str], new: dict[str, str]) -> dict:
    """
    Compare old and new resource availability states.
    Returns dict with 'added', 'removed', 'changed' lists.
    """
    old_names = set(old.keys())
    new_names = set(new.keys())

    added = []
    for name in sorted(new_names - old_names):
        added.append({"name": name, "status": new[name]})

    removed = sorted(old_names - new_names)

    # Check for status changes (e.g., isPlannedIn -> isAvailableIn)
    changed = []
    for name in sorted(old_names & new_names):
        if old[name] != new[name]:
            changed.append({
                "name": name,
                "old_status": old[name],
                "new_status": new[name],
            })

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def has_changes(changes: dict) -> bool:
    """Check if there are any changes."""
    return bool(changes["added"] or changes["removed"] or changes["changed"])


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def friendly_status(status: str) -> str:
    """Convert raw status to friendly label."""
    return STATUS_LABELS.get(status, status)


def group_apis_by_service(items: list[dict] | list[str]) -> dict[str, list[str]]:
    """
    Group API names by their service prefix.

    API names are formatted as "ServiceName+OperationName".
    Returns {service_name: [operation_names]}
    """
    groups = {}
    for item in items:
        # Handle both dict (added/changed) and str (removed)
        name = item["name"] if isinstance(item, dict) else item
        if "+" in name:
            service, operation = name.split("+", 1)
            if service not in groups:
                groups[service] = []
            groups[service].append(operation)
        else:
            # No service prefix, use as-is
            if "_other" not in groups:
                groups["_other"] = []
            groups["_other"].append(name)
    return groups


def format_markdown_report(
    results: dict[str, dict[str, dict]],
    is_first_run: dict[str, dict[str, bool]],
    region_changes: dict | None = None,
    region_first_run: bool = False,
) -> str:
    """
    Format results as markdown.

    Args:
        results: {region: {resource_type: changes_dict}}
        is_first_run: {region: {resource_type: bool}}
        region_changes: changes dict for global regions (added/removed)
        region_first_run: whether this is the first run for region tracking
    """
    lines = []

    # Report header
    lines.append("# AWS Region Watch Report")
    lines.append("")

    # Global regions section (if tracking regions and not first run)
    if region_changes is not None and not region_first_run:
        if has_changes(region_changes):
            lines.append("## New AWS Regions")
            lines.append("")

            if region_changes["added"]:
                for item in region_changes["added"]:
                    # item is {name: region_id, status: long_name}
                    lines.append(f"- **{item['name']}** - {item['status']}")
                lines.append("")

            if region_changes["removed"]:
                lines.append("Removed:")
                lines.append("")
                for name in region_changes["removed"]:
                    lines.append(f"- {name}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Order: products first, then APIs
    type_order = ["product", "api"]

    for region, type_results in results.items():
        region_display = get_region_display_name(region)
        region_long = get_region_names().get(region, region)

        type_config = {
            "product": {
                "label": "Products/Features",
                "description": f"AWS services and features available in {region_long}.",
            },
            "api": {
                "label": "APIs",
                "description": f"Individual SDK API operations available in {region_long}. Grouped by service.",
            },
        }

        region_has_content = False

        for resource_type in type_order:
            if resource_type not in type_results:
                continue

            changes = type_results[resource_type]
            config = type_config.get(resource_type, {"label": resource_type, "description": ""})
            first_run = is_first_run.get(region, {}).get(resource_type, False)

            # Skip first run and no-changes cases
            if first_run or not has_changes(changes):
                continue

            # Add region heading on first content
            if not region_has_content:
                lines.append(f"## {region_display}")
                lines.append("")
                region_has_content = True

            lines.append(f"### {config['label']}")
            lines.append("")
            lines.append(config["description"])
            lines.append("")

            if changes["added"]:
                lines.append(f"#### New ({len(changes['added'])})")
                lines.append("")

                if resource_type == "api":
                    # Group APIs by service
                    groups = group_apis_by_service(changes["added"])
                    for service in sorted(groups.keys()):
                        operations = groups[service]
                        lines.append(f"**{service}** ({len(operations)})")
                        for op in sorted(operations):
                            lines.append(f"- {op}")
                        lines.append("")
                else:
                    # Products: show with status
                    for item in changes["added"]:
                        status = friendly_status(item["status"])
                        lines.append(f"- **{item['name']}** - {status}")
                    lines.append("")

            if changes["changed"]:
                lines.append(f"#### Status Changes ({len(changes['changed'])})")
                lines.append("")

                if resource_type == "api":
                    # Group APIs by service (unlikely to have status changes, but handle it)
                    groups = group_apis_by_service(changes["changed"])
                    for service in sorted(groups.keys()):
                        operations = groups[service]
                        lines.append(f"**{service}** ({len(operations)})")
                        for op in sorted(operations):
                            lines.append(f"- {op}")
                        lines.append("")
                else:
                    for change in changes["changed"]:
                        old = friendly_status(change["old_status"])
                        new = friendly_status(change["new_status"])
                        lines.append(f"- **{change['name']}**: {old} → {new}")
                    lines.append("")

            if changes["removed"]:
                lines.append(f"#### Removed ({len(changes['removed'])})")
                lines.append("")

                if resource_type == "api":
                    # Group APIs by service
                    groups = group_apis_by_service(changes["removed"])
                    for service in sorted(groups.keys()):
                        operations = groups[service]
                        lines.append(f"**{service}** ({len(operations)})")
                        for op in sorted(operations):
                            lines.append(f"- {op}")
                        lines.append("")
                else:
                    for name in changes["removed"]:
                        lines.append(f"- {name}")
                    lines.append("")

        # Only add separator if region had content
        if region_has_content:
            lines.append("---")
            lines.append("")

    # Report footer
    # Check if we have any content at all (regions or per-region changes)
    has_any_content = len(lines) > 2  # More than just header

    if not has_any_content:
        # No changes to report - return empty string
        return ""

    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by [AWS Region Watch](https://github.com/rodos/aws-region-watch)*")
    lines.append("")
    lines.append(f"*Data source: [{MCP_API_URL}]({MCP_API_URL})*")

    return "\n".join(lines)


def format_json_report(
    results: dict[str, dict[str, dict]],
    is_first_run: dict[str, dict[str, bool]],
    region_changes: dict | None = None,
    region_first_run: bool = False,
) -> str:
    """
    Format results as JSON.

    Args:
        results: {region: {resource_type: changes_dict}}
        is_first_run: {region: {resource_type: bool}}
        region_changes: changes dict for global regions (added/removed)
        region_first_run: whether this is the first run for region tracking

    Returns empty string if no changes (first run or no changes detected).
    """
    output = {
        "timestamp": datetime.now().isoformat(),
        "source": MCP_API_URL,
    }

    has_any_changes = False

    # Global regions (skip if first run)
    if region_changes is not None and not region_first_run:
        if has_changes(region_changes):
            output["global_regions"] = {
                "added": region_changes["added"],
                "removed": region_changes["removed"],
            }
            has_any_changes = True

    # Per-region data (skip first run entries)
    output["regions"] = {}
    for region, type_results in results.items():
        region_data = {}
        for resource_type, changes in type_results.items():
            first_run = is_first_run.get(region, {}).get(resource_type, False)
            if not first_run and has_changes(changes):
                region_data[resource_type] = {
                    "added": changes["added"],
                    "changed": changes["changed"],
                    "removed": changes["removed"],
                }
                has_any_changes = True

        if region_data:
            output["regions"][region] = region_data

    # Return empty if no changes
    if not has_any_changes:
        return ""

    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_types(value: str) -> list[str]:
    """Parse comma-separated type list and validate."""
    types = [t.strip().lower() for t in value.split(",")]
    invalid = set(types) - VALID_TYPES
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid type(s): {', '.join(invalid)}. Valid types: {', '.join(sorted(VALID_TYPES))}"
        )
    return types


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Monitor AWS service/feature availability in regions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --region ap-southeast-2 --region us-west-2
  %(prog)s --type api
  %(prog)s --type api,product
  %(prog)s --format json > changes.json
  %(prog)s --quiet --format json
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--region",
        action="append",
        dest="regions",
        metavar="REGION",
        help="AWS region to monitor (can be specified multiple times). "
             "Default: from AWS_REGION, AWS_DEFAULT_REGION, or ~/.aws/config",
    )

    parser.add_argument(
        "--type",
        type=parse_types,
        default=DEFAULT_TYPES,
        metavar="TYPE",
        help="Resource types to monitor (comma-separated). "
             f"Valid types: {', '.join(sorted(VALID_TYPES))}. "
             f"Default: {','.join(DEFAULT_TYPES)}",
    )

    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        metavar="DIR",
        help=f"Directory for state files. Default: {DEFAULT_STATE_DIR}",
    )

    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format. Default: markdown",
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress messages (only output report)",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed progress information",
    )

    args = parser.parse_args()

    # Default region if none specified
    if not args.regions:
        default_region = get_default_region()
        if default_region:
            args.regions = [default_region]
        else:
            parser.error(
                "No region specified and no default region found.\n"
                "Set a region with --region, AWS_REGION, AWS_DEFAULT_REGION, "
                "or configure ~/.aws/config"
            )

    return args


def main():
    """Main entry point."""
    global log

    args = parse_args()
    log = Logger(quiet=args.quiet, verbose=args.verbose)

    log.progress("AWS Region Watch")
    log.progress("=" * 40)

    type_labels = {"product": "products", "api": "APIs", "region": "regions"}

    global _http_client
    with httpx.Client() as client:
        _http_client = client
        try:
            results = {}  # {region: {type: changes}}
            is_first_run = {}  # {region: {type: bool}}
            any_changes = False

            # Handle global region tracking (if "region" is in types)
            region_changes = None
            region_first_run = False
            track_regions = "region" in args.type

            if track_regions:
                log.progress("\nFetching global regions...")
                current_regions = fetch_all_regions()
                log.progress(f"  Found {len(current_regions)} regions")

                # Load previous global state
                global_state = load_global_state(args.state_dir)
                previous_regions = global_state.get("region", {})

                if previous_regions:
                    region_changes = compare_states(previous_regions, current_regions)
                    region_first_run = False
                    if has_changes(region_changes):
                        any_changes = True
                else:
                    log.progress("  First run - establishing baseline")
                    region_changes = {"added": [], "removed": [], "changed": []}
                    region_first_run = True

                # Save global state
                global_state["region"] = current_regions
                save_global_state(global_state, args.state_dir)
                log.progress(f"  Global state saved to {get_global_state_file(args.state_dir)}")

            # Per-region resource types (product, api)
            per_region_types = [t for t in args.type if t != "region"]

            for region in args.regions:
                if per_region_types:
                    log.progress(f"\nFetching data for {region}...")
                    results[region] = {}
                    is_first_run[region] = {}

                    # Load previous state for this region
                    state = load_region_state(args.state_dir, region)

                    for resource_type in per_region_types:
                        type_label = type_labels.get(resource_type, resource_type)
                        log.progress(f"  Fetching {type_label}...")

                        # Fetch current resources
                        current = fetch_region_resources(region, resource_type)
                        log.progress(f"    Found {len(current)} {type_label}")

                        # Count by status
                        available = sum(1 for s in current.values() if s == "isAvailableIn")
                        planned = sum(1 for s in current.values() if s in ("isPlannedIn", "isBeingPlannedIn"))
                        log.progress(f"    Available: {available}, Planned: {planned}")

                        # Get previous state for this type
                        previous = state.get(resource_type, {})

                        # Compare and report
                        if previous:
                            changes = compare_states(previous, current)
                            results[region][resource_type] = changes
                            is_first_run[region][resource_type] = False
                            if has_changes(changes):
                                any_changes = True
                        else:
                            log.progress(f"    First run - establishing baseline")
                            results[region][resource_type] = {"added": [], "removed": [], "changed": []}
                            is_first_run[region][resource_type] = True

                        # Update state
                        state[resource_type] = current

                    # Save state for this region
                    save_region_state(state, args.state_dir, region)
                    log.progress(f"  State saved to {get_state_file(args.state_dir, region)}")

            # Output report to stdout (empty if no changes/first run)
            if args.format == "json":
                report = format_json_report(results, is_first_run, region_changes, region_first_run)
            else:
                report = format_markdown_report(results, is_first_run, region_changes, region_first_run)

            if report:
                print(report)

            # Exit code: 0 = no changes, 1 = changes detected
            sys.exit(EXIT_CHANGES if any_changes else EXIT_SUCCESS)

        except APIError as e:
            log.error(str(e))
            sys.exit(EXIT_ERROR)

        except KeyboardInterrupt:
            log.error("Interrupted by user")
            sys.exit(EXIT_ERROR)

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()

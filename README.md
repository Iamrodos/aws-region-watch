# AWS Region Watch

Monitor AWS service and feature availability in your regions. Get notified when new AWS regions launch and new capabilities become available.

## Quick Start

### Using uv (recommended)

```bash
# Install from GitHub and run
uvx --from git+https://github.com/Iamrodos/aws-region-watch aws-region-watch

# Or install globally
uv tool install git+https://github.com/Iamrodos/aws-region-watch
aws-region-watch
```

### Using pip

```bash
# Install from GitHub
pip install git+https://github.com/Iamrodos/aws-region-watch

# Run
aws-region-watch
```

## Usage

```bash
# Monitor your default AWS region (from AWS_REGION, AWS_DEFAULT_REGION, or ~/.aws/config)
aws-region-watch

# Monitor specific regions
aws-region-watch --region ap-southeast-2 --region us-west-2

# Monitor APIs instead of products (more granular, ~15k items)
aws-region-watch --type api

# Monitor all resource types (regions, products, APIs)
aws-region-watch --type region,product,api

# Skip global region tracking
aws-region-watch --type product

# Output as JSON
aws-region-watch --format json

# Quiet mode (report only, no progress)
aws-region-watch --quiet

# Verbose mode (show API details)
aws-region-watch --verbose

# Custom state directory
aws-region-watch --state-dir /path/to/state
```

## Options

| Option | Description |
|--------|-------------|
| `--region REGION` | AWS region to monitor (can be repeated). Default: from AWS_REGION, AWS_DEFAULT_REGION, or ~/.aws/config |
| `--type TYPE` | Resource types to monitor (comma-separated: `region`, `product`, `api`). Default: region,product |
| `--state-dir DIR` | Directory for state files. Default: state/ |
| `--format {markdown,json}` | Output format. Default: markdown |
| `-q, --quiet` | Suppress progress messages |
| `-v, --verbose` | Show detailed progress |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success, no changes detected |
| 1 | Success, changes detected |
| 2 | Error (network, API, etc.) |

## Output Examples

### Markdown (default)

```markdown
# AWS Region Watch Report

## New AWS Regions

- **mx-central-1** - Mexico (Central)

---

## ap-southeast-2 - Asia Pacific (Sydney)

### Products/Features

AWS services and features available in Asia Pacific (Sydney).

#### New (2)

- **Lambda SnapStart** - Available
- **Amazon Bedrock Agents** - Available

#### Status Changes (1)

- **AWS Resilience Hub**: Planned → Available

### APIs

Individual SDK API operations available in Asia Pacific (Sydney). Grouped by service.

#### New (5)

**Bedrock** (3)
- CreateFlow
- GetFlow
- ListFlows

**Lambda** (2)
- GetDurableExecution
- ListDurableExecutions

---

*Generated 2024-12-06 09:15:32 by [AWS Region Watch](https://github.com/Iamrodos/aws-region-watch)*

*Data source: [https://knowledge-mcp.global.api.aws](https://knowledge-mcp.global.api.aws)*
```

### JSON

```json
{
  "timestamp": "2024-12-06T09:15:32",
  "source": "https://knowledge-mcp.global.api.aws",
  "global_regions": {
    "first_run": false,
    "added": [
      {"name": "mx-central-1", "status": "Mexico (Central)"}
    ],
    "removed": []
  },
  "regions": {
    "ap-southeast-2": {
      "product": {
        "first_run": false,
        "added": [
          {"name": "Lambda SnapStart", "status": "isAvailableIn"}
        ],
        "changed": [
          {"name": "AWS Resilience Hub", "old_status": "isPlannedIn", "new_status": "isAvailableIn"}
        ],
        "removed": []
      },
      "api": {
        "first_run": false,
        "added": [
          {"name": "Bedrock+CreateFlow", "status": "isAvailableIn"},
          {"name": "Lambda+GetDurableExecution", "status": "isAvailableIn"}
        ],
        "changed": [],
        "removed": []
      }
    }
  }
}
```

## Data Source

This tool uses the [AWS Knowledge MCP API](https://knowledge-mcp.global.api.aws), which provides:

- **Regions** (~37 items) - AWS regions globally
- **Products** (~1,300 items) - AWS services and features per region
- **APIs** (~15,000 items) - Individual SDK API operations per region

The API only lists regions once they are launched - announced but not yet available regions (e.g., "coming soon") do not appear. Availability Zones are not tracked by this API; AZ information requires AWS credentials via the EC2 `describe-availability-zones` API.

AWS also publishes a [Regional Services List](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/) on their website. Whether that page uses the same underlying data source as this API is unknown.

### Status Values

| Status | Meaning |
|--------|---------|
| Available | Live in the region |
| Planning | Evaluating launch strategy |
| Not Expanding | Will not launch in region |

Source: [Introducing AWS Capabilities by Region](https://aws.amazon.com/blogs/aws/introducing-aws-capabilities-by-region-for-easier-regional-planning-and-faster-global-deployments/) (AWS News Blog, Nov 2025)

### Data Accuracy

The API is maintained by AWS, but we don't have visibility into:

- **Update frequency** - How often AWS refreshes the data
- **Lag time** - Delay between a service launching and appearing in the API
- **Completeness** - Whether all services/features are represented

If you notice discrepancies between this tool's output and actual service availability, the API data may be stale or incomplete. Always verify critical availability information through official AWS channels such as the [Regional Services List](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/) or [AWS Capabilities by Region](https://builder.aws.com/capabilities/).

## GitHub Actions Setup

A handly place to run this is as a scheduled GitHub action. You can use this to get notified when AWS services become available in your regions. See [`.github/workflows/watch.yml`](.github/workflows/watch.yml) for a working example.

### State Storage

The tool needs to persist state between runs to detect changes. The example workflow stores state in a separate orphan `state` branch - this keeps state versioned and auditable without polluting main branch history.

Other persistence options (S3, artifact storage, etc.) are left as an exercise for the reader.

### Notifications

The example workflow creates GitHub Issues, but you can also use Slack, email, Discord, Teams, or any webhook.

## Future Ideas

- **GitHub Action setup wizard** - Interactive `--init-action` command that generates a ready-to-use workflow file with your preferred regions, schedule, and notification method
- **CloudFormation resources** - Add `--type cfn` to track CloudFormation resource availability

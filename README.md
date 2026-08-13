# Google Search Console MCP Server

An MCP (Model Context Protocol) server that provides tools to query Google Search Console data from AI assistants like Claude.

## Features

- **Search Analytics** -- Query clicks, impressions, CTR, and position with filtering by query, page, country, device, and date
- **URL Inspection** -- Check indexation status, crawl info, canonical URLs, mobile usability, and rich results
- **Sitemap Management** -- List, get details, submit, and delete sitemaps
- **Site Listing** -- List all verified properties and their permission levels

## Tools

| Tool | Description | Read-only |
|---|---|---|
| `gsc_list_sites` | List all verified Search Console properties | Yes |
| `gsc_search_analytics` | Query search performance data with dimensions and filters | Yes |
| `gsc_inspect_url` | Inspect a URL's index status, crawl, and rich results | Yes |
| `gsc_list_sitemaps` | List submitted sitemaps for a property | Yes |
| `gsc_get_sitemap` | Get details for a specific sitemap | Yes |
| `gsc_submit_sitemap` | Submit a sitemap (requires `GSC_WRITE_ACCESS=true`) | No |
| `gsc_delete_sitemap` | Delete a submitted sitemap (requires `GSC_WRITE_ACCESS=true`) | No |

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Google Cloud credentials with Search Console API access

### Install dependencies

```bash
git clone https://github.com/kn00m1/google-search-console-mcp.git
cd google-search-console-mcp
uv sync
```

## Authentication

The server tries credentials in this order:

1. **Cached token** (`~/.config/gsc-mcp/token.json`) -- from a previous OAuth flow
2. **Application Default Credentials (ADC)** -- from `gcloud auth application-default login`
3. **OAuth2 browser flow** -- using `~/.config/gsc-mcp/client_secrets.json`

### Quickest setup (ADC)

```bash
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/webmasters.readonly
```

### OAuth2 setup (if ADC doesn't cover your scopes)

1. Create an OAuth client ID (Desktop app) in [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Download the JSON and save it as `~/.config/gsc-mcp/client_secrets.json`
3. The server will open a browser for consent on first run

## Usage

### With Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "google-search-console": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/google-search-console-mcp", "python", "-m", "gsc_mcp.server"],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

### With any MCP client

```bash
uv run python -m gsc_mcp.server
```

The server communicates over stdio using the MCP protocol.

### Enable write access

Set the environment variable to unlock sitemap submit/delete tools:

```bash
GSC_WRITE_ACCESS=true uv run python -m gsc_mcp.server
```

## Security

- Token files are stored with `0600` permissions
- API responses are sanitized against prompt injection patterns
- Error messages redact OAuth tokens and API keys
- Write tools (submit/delete sitemap) are disabled by default

## License

MIT
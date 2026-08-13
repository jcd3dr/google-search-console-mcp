#!/bin/bash
set -e

# Create config directory
mkdir -p /root/.config/gsc-mcp

# If credentials are provided as JSON environment variable, write them to file
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
  echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /root/.config/gsc-mcp/credentials.json
  chmod 600 /root/.config/gsc-mcp/credentials.json
  export GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gsc-mcp/credentials.json
fi

# Start the MCP server
exec uv run python -m gsc_mcp.server

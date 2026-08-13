FROM python:3.11-slim

WORKDIR /app

# Install git and uv
RUN apt-get update && apt-get install -y -q git && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN pip install -q uv

# Clone the repository
RUN git clone https://github.com/jcd3dr/google-search-console-mcp.git .

# Install dependencies
RUN uv sync

# Create entrypoint script inline
RUN cat > /entrypoint.sh << 'ENTRYPOINTEOF'
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
ENTRYPOINTEOF

RUN chmod +x /entrypoint.sh

# Set environment variable for write access
ENV GSC_WRITE_ACCESS=true

# Expose port for MCP server
EXPOSE 3000

# Use entrypoint script to handle credentials initialization
ENTRYPOINT ["/entrypoint.sh"]

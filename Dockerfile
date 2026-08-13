FROM python:3.11-slim

WORKDIR /app

# Install uv package manager
RUN pip install -q uv

# Copy project files
COPY . .

# Install dependencies
RUN uv sync

# Set environment variable for write access
ENV GSC_WRITE_ACCESS=true

# Create credentials file from environment variable at runtime
RUN mkdir -p /root/.config/gsc-mcp

# Expose port for MCP server
EXPOSE 3000

# Entry script to write credentials and start the server
COPY <<EOF /entrypoint.sh
#!/bin/bash
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
  echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /root/.config/gsc-mcp/credentials.json
  export GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gsc-mcp/credentials.json
fi
exec uv run python -m gsc_mcp.server
EOF

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

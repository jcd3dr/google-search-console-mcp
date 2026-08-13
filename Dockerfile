FROM python:3.11-slim

WORKDIR /app

# Install uv package manager
RUN pip install -q uv

# Copy project files
COPY . .

# Install dependencies
RUN uv sync

# Copy and make entrypoint script executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set environment variable for write access
ENV GSC_WRITE_ACCESS=true

# Expose port for MCP server
EXPOSE 3000

# Use entrypoint script to handle credentials initialization
ENTRYPOINT ["/entrypoint.sh"]

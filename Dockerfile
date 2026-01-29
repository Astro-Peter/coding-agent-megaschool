FROM python:3.11-slim

WORKDIR /app

# Install git for repository operations
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better caching
COPY pyproject.toml README.md ./

# Install package in editable mode
COPY agents/ ./agents/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .

# Default command runs the unified watcher
CMD ["python", "-m", "scripts.watcher"]

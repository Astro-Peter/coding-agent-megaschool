FROM python:3.11-slim

WORKDIR /app

# Install git for repository operations
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better caching
COPY pyproject.toml README.md ./

# Install package in editable mode
COPY github_agents/ ./github_agents/
RUN pip install --no-cache-dir -e .

# Default command runs the coder agent from plan
CMD ["python", "-m", "github_agents.coder_agent.run_from_plan"]

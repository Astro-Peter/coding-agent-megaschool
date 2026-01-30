"""Shared configuration and client setup for all agents."""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

from agents.common.github_client import GitHubClient


@dataclass
class Config:
    """Common configuration for all agents.
    
    Note: With the OpenAI Agents SDK migration, the LLM client is configured
    globally via sdk_config.configure_sdk() rather than passed as a parameter.
    """
    gh_client: GitHubClient
    model: str


def _require_env(name: str) -> str:
    """Get required environment variable or exit."""
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}")
        sys.exit(1)
    return value


def load_config() -> Config:
    """Load configuration from environment variables.
    
    Required environment variables:
    - GH_TOKEN: GitHub personal access token
    - GH_REPOSITORY: Repository in format owner/repo
    - LLM_API_TOKEN: API token for the LLM provider
    
    Optional environment variables:
    - LLM_API_URL: Base URL for the LLM API (defaults to OpenRouter)
    - LLM_MODEL: Model name to use (defaults to gpt-4o-mini)
    - LOG_LEVEL: Logging level (defaults to INFO)
    """
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    
    token = _require_env("GH_TOKEN")
    repo = _require_env("GH_REPOSITORY")
    # Validate LLM token is present (used by sdk_config)
    _require_env("LLM_API_TOKEN")
    llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    
    return Config(
        gh_client=GitHubClient(token=token, repo_full_name=repo),
        model=llm_model,
    )


def get_issue_number() -> int:
    """Get ISSUE_NUMBER from environment."""
    raw = _require_env("ISSUE_NUMBER")
    try:
        return int(raw)
    except ValueError:
        print("ISSUE_NUMBER must be an integer.")
        sys.exit(1)


def get_pr_number() -> int:
    """Get PR_NUMBER from environment."""
    raw = _require_env("PR_NUMBER")
    try:
        return int(raw)
    except ValueError:
        print("PR_NUMBER must be an integer.")
        sys.exit(1)

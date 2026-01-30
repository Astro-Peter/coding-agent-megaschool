"""GitHub Agents package.

This package provides AI-powered agents for the SDLC workflow:
- Planner: Creates implementation plans from GitHub issues
- Coder: Implements code changes based on plans
- Reviewer: Reviews pull requests for quality

Built on the OpenAI Agents SDK.
"""

from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk
from github_agents.orchestrator import Orchestrator

__all__ = [
    "AgentContext",
    "Orchestrator",
    "configure_sdk",
]

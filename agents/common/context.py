"""Shared context for all agents using the OpenAI Agents SDK."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.common.code_index import CodeIndex
    from agents.common.github_client import GitHubClient


@dataclass
class AgentContext:
    """Shared context passed to all agents via dependency injection.
    
    This context provides access to:
    - GitHub client for API operations
    - Model name for LLM calls
    - Workspace path for file operations
    - Code index for searching
    - Issue/PR numbers for tracking
    """
    gh_client: GitHubClient
    model: str
    workspace: Path | None = None
    index: CodeIndex | None = None
    issue_number: int | None = None
    pr_number: int | None = None
    iteration: int = 1
    max_iterations: int = 5
    reviewer_feedback: list[str] = field(default_factory=list)

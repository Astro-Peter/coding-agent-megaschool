"""Shared utilities for coder agent runners.

This module contains common patterns used across the different entrypoints
to avoid code duplication.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from github_agents.common.code_index import CodeIndex
from github_agents.common.context import AgentContext

if TYPE_CHECKING:
    from github_agents.common.github_client import GitHubClient

logger = logging.getLogger(__name__)


@dataclass
class BranchInfo:
    """Information about the branch to work on."""

    branch_name: str
    is_update: bool


@contextmanager
def temp_clone_directory(prefix: str = "coder_agent_"):
    """Context manager for creating and cleaning up a temporary clone directory.

    Args:
        prefix: Prefix for the temp directory name.

    Yields:
        Path to the repo directory within the temp directory.
    """
    temp_dir = tempfile.mkdtemp(prefix=prefix)
    clone_path = Path(temp_dir) / "repo"
    try:
        yield clone_path
    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def setup_ci_fix_mode(context: AgentContext) -> None:
    """Check and configure CI fix mode from environment.

    Sets context.is_ci_fix_mode based on the CI_FIX_MODE env var.
    If in CI fix mode and a PR number is available, loads CI feedback.

    Args:
        context: The agent context to configure.
    """
    from github_agents.coder_agent.agent import _load_latest_ci_feedback

    is_ci_fix_mode = os.getenv("CI_FIX_MODE", "").lower() == "true"
    context.is_ci_fix_mode = is_ci_fix_mode

    if is_ci_fix_mode and context.pr_number:
        client = context.gh_client
        pr_comments = client.list_pr_comments(context.pr_number)
        ci_feedback = _load_latest_ci_feedback(pr_comments)
        context.ci_feedback = ci_feedback
        if ci_feedback:
            logger.info(
                "Loaded %d CI feedback items from PR #%d",
                len(ci_feedback),
                context.pr_number,
            )


def setup_context_for_workspace(context: AgentContext, clone_path: Path) -> None:
    """Set up the agent context with workspace and code index.

    Args:
        context: The agent context to configure.
        clone_path: Path to the cloned repository.
    """
    context.workspace = clone_path
    index = CodeIndex(str(clone_path))
    index.build()
    context.index = index


def get_clone_token() -> str:
    """Get the GitHub token for cloning."""
    return os.getenv("GH_TOKEN", "")


def determine_branch_for_issue(
    client: GitHubClient,
    issue_number: int,
    *,
    is_ci_fix_mode: bool = False,
    pr_number: int | None = None,
) -> BranchInfo:
    """Determine which branch to use for an issue.

    In CI fix mode with a PR, uses the existing PR branch.
    Otherwise, checks for an existing coder-agent branch or creates a new one.

    Args:
        client: GitHub client.
        issue_number: The issue number.
        is_ci_fix_mode: Whether in CI fix mode.
        pr_number: Optional PR number.

    Returns:
        BranchInfo with branch name and whether it's an update.
    """
    from github_agents.coder_agent.agent import _find_existing_branch

    if is_ci_fix_mode and pr_number:
        pr_info = client.get_pull_request(pr_number)
        existing_branch = pr_info.head_ref
        logger.info("CI fix mode: using existing PR branch %s", existing_branch)
        return BranchInfo(branch_name=existing_branch, is_update=True)

    existing_branch = _find_existing_branch(client, issue_number)
    if existing_branch:
        return BranchInfo(branch_name=existing_branch, is_update=True)

    return BranchInfo(
        branch_name=f"coder-agent/issue-{issue_number}-{secrets.token_hex(4)}",
        is_update=False,
    )


def generate_new_branch_name(issue_number: int) -> str:
    """Generate a new branch name for an issue.

    Args:
        issue_number: The issue number.

    Returns:
        A new branch name with a random suffix.
    """
    return f"coder-agent/issue-{issue_number}-{secrets.token_hex(4)}"


def load_plan_from_issue(client: GitHubClient, issue_number: int) -> dict | None:
    """Load the latest plan from issue comments.

    Args:
        client: GitHub client.
        issue_number: The issue number.

    Returns:
        The plan dict if found, None otherwise.
    """
    from github_agents.coder_agent.agent import _load_latest_plan

    comments = client.list_issue_comments(issue_number)
    return _load_latest_plan(comments)


def load_comment_history_from_pr(client: GitHubClient, pr_number: int) -> list[dict]:
    """Load comment history from a PR.

    Args:
        client: GitHub client.
        pr_number: The PR number.

    Returns:
        List of comment dicts with 'author', 'body', 'created_at' keys.
    """
    pr_comments = client.list_pr_comments(pr_number)
    return [
        {
            "author": comment.user_login,
            "body": comment.body,
            "created_at": (
                comment.created_at.isoformat()
                if hasattr(comment.created_at, "isoformat")
                else str(comment.created_at)
            ),
        }
        for comment in sorted(pr_comments, key=lambda c: c.created_at)
    ]


def load_ci_feedback_from_pr(
    context: AgentContext, client: GitHubClient, pr_number: int
) -> list[dict] | None:
    """Load CI feedback from PR comments if in CI fix mode.

    Args:
        context: Agent context (will be updated with ci_feedback).
        client: GitHub client.
        pr_number: The PR number.

    Returns:
        List of CI feedback items if found, None otherwise.
    """
    from github_agents.coder_agent.agent import _load_latest_ci_feedback

    pr_comments = client.list_pr_comments(pr_number)
    ci_feedback = _load_latest_ci_feedback(pr_comments)
    if ci_feedback:
        context.ci_feedback = ci_feedback
        logger.info("Loaded %d CI feedback items from PR #%d", len(ci_feedback), pr_number)
    return ci_feedback

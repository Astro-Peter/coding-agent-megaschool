"""PR comments-based entrypoint for the Coder Agent.

This entrypoint runs the coder agent based on comment history on an existing PR.
It commits directly into the existing PR branch.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from github_agents.common.code_index import CodeIndex
from github_agents.common.config import load_config
from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk

from github_agents.coder_agent.agent import (
    MAX_DEV_ITERATIONS,
    _clone_repository,
    _git_commit,
    _git_push,
    _load_latest_ci_feedback,
    run_coder_agent_from_pr_comments_async,
)

logger = logging.getLogger(__name__)


async def run_coder_from_pr_async(*, context: AgentContext) -> None:
    """Entry point for the coder agent when working from PR comments.
    
    This entrypoint is used when the agent should commit into an existing PR branch
    based on the comment history rather than following a plan from an issue.
    
    Requires context.pr_number to be set.
    """
    if context.pr_number is None:
        raise ValueError("pr_number is required in context for PR comments mode")
    
    client = context.gh_client
    pr_number = context.pr_number
    
    # Check if we're in CI fix mode
    is_ci_fix_mode = os.getenv("CI_FIX_MODE", "").lower() == "true"
    context.is_ci_fix_mode = is_ci_fix_mode
    
    # Get PR details
    pr_info = client.get_pull_request(pr_number)
    branch_name = pr_info.head_ref
    
    # Load PR comments as comment history
    pr_comments = client.list_pr_comments(pr_number)
    comment_history = [
        {
            'author': comment.user_login,
            'body': comment.body,
            'created_at': comment.created_at.isoformat() if hasattr(comment.created_at, 'isoformat') else str(comment.created_at),
        }
        for comment in sorted(pr_comments, key=lambda c: c.created_at)
    ]
    
    # If in CI fix mode, also extract CI feedback
    if is_ci_fix_mode:
        ci_feedback = _load_latest_ci_feedback(pr_comments)
        context.ci_feedback = ci_feedback
        if ci_feedback:
            logger.info("Loaded %d CI feedback items from PR #%d", len(ci_feedback), pr_number)
    
    # Set default iteration values
    context.iteration = context.iteration or 1
    context.max_iterations = context.max_iterations or MAX_DEV_ITERATIONS
    
    # Create temporary directory for clone
    temp_dir = tempfile.mkdtemp(prefix="coder_agent_pr_")
    clone_path = Path(temp_dir) / "repo"

    try:
        clone_url = client.get_clone_url()
        token = os.getenv("GH_TOKEN", "")

        logger.info("Coder Agent working on PR #%d, branch %s", pr_number, branch_name)

        # Clone directly with the PR branch
        if not _clone_repository(clone_url, token, clone_path, branch=branch_name):
            logger.error("Failed to clone repository for PR #%d", pr_number)
            return

        # Set up context for tools
        context.workspace = clone_path
        index = CodeIndex(str(clone_path))
        index.build()
        context.index = index

        # Run the agent with PR comments mode
        summary = await run_coder_agent_from_pr_comments_async(
            pr_title=pr_info.title,
            pr_body=pr_info.body or "",
            branch_name=branch_name,
            comment_history=comment_history,
            context=context,
        )

        # Commit and push changes
        commit_message = f"fix: address PR feedback for #{pr_number}\n\n{summary}"
        has_changes = _git_commit(clone_path, commit_message)

        if has_changes:
            if _git_push(clone_path, branch_name):
                logger.info("Pushed changes to PR #%d branch %s", pr_number, branch_name)
            else:
                logger.error("Failed to push changes to PR #%d", pr_number)
        else:
            logger.info("No changes to commit for PR #%d", pr_number)

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def run_coder_from_pr(*, context: AgentContext) -> None:
    """Synchronous wrapper for run_coder_from_pr_async."""
    asyncio.run(run_coder_from_pr_async(context=context))


def main() -> int:
    """CLI entry point for the PR comments-based coder agent."""
    cfg = load_config()
    
    # Configure the SDK for OpenRouter
    configure_sdk()
    
    # Get PR number from environment
    pr_number_str = os.getenv("PR_NUMBER", "")
    if not pr_number_str:
        raise ValueError("PR_NUMBER environment variable is required")
    
    try:
        pr_number = int(pr_number_str)
    except ValueError:
        raise ValueError(f"PR_NUMBER must be an integer, got: {pr_number_str}")
    
    # Create context
    context = AgentContext(
        gh_client=cfg.gh_client,
        model=cfg.model,
        pr_number=pr_number,
    )
    
    run_coder_from_pr(context=context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

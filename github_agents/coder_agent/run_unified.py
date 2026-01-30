"""Unified entrypoint for the Coder Agent.

This entrypoint can receive either a plan or PR comment history and routes
to the appropriate mode.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from github_agents.common.code_index import CodeIndex
from github_agents.common.context import AgentContext

from github_agents.coder_agent.agent import (
    MAX_DEV_ITERATIONS,
    _clone_repository,
    _find_existing_branch,
    _git_checkout_existing_branch,
    _git_commit,
    _git_create_branch,
    _git_push,
    _load_latest_ci_feedback,
    run_coder_agent_async,
    run_coder_agent_from_pr_comments_async,
)
from github_agents.coder_agent.run_from_plan import run_coder_async

logger = logging.getLogger(__name__)


async def run_coder_unified_async(
    *,
    context: AgentContext,
    plan: dict | None = None,
    pr_comment_history: list[dict] | None = None,
) -> None:
    """Unified entry point that accepts either a plan or PR comment history.
    
    This is the main entrypoint for the coder agent that can operate in two modes:
    
    1. Plan mode: When `plan` is provided, the agent follows the plan to implement changes.
       Requires context.issue_number to be set.
    
    2. PR comments mode: When `pr_comment_history` is provided, the agent addresses
       feedback from the comment history and commits into the existing PR branch.
       Requires context.pr_number to be set.
    
    If both are provided, PR comments mode takes precedence (assumes we're iterating on an existing PR).
    If neither is provided, falls back to loading plan from issue comments.
    
    Args:
        context: The agent context with GitHub client and other settings.
        plan: Optional plan dict with 'summary' and 'steps' keys.
        pr_comment_history: Optional list of comment dicts with 'author', 'body', 'created_at' keys.
    """
    # Determine which mode to use
    use_pr_comments_mode = pr_comment_history is not None and context.pr_number is not None
    
    if use_pr_comments_mode:
        # PR comments mode - commit into existing branch based on comment feedback
        await _run_coder_pr_comments_mode(
            context=context,
            comment_history=pr_comment_history,
        )
    else:
        # Plan mode - either use provided plan or load from issue comments
        if plan is not None:
            await _run_coder_plan_mode(context=context, plan=plan)
        else:
            # Fall back to the original behavior - load plan from issue comments
            await run_coder_async(context=context)


async def _run_coder_pr_comments_mode(
    *,
    context: AgentContext,
    comment_history: list[dict],
) -> None:
    """Internal: Run coder in PR comments mode."""
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
    
    # If in CI fix mode, also extract CI feedback from comments
    if is_ci_fix_mode:
        pr_comments = client.list_pr_comments(pr_number)
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

        logger.info("Coder Agent working on PR #%d, branch %s (PR comments mode)", pr_number, branch_name)

        if not _clone_repository(clone_url, token, clone_path):
            logger.error("Failed to clone repository for PR #%d", pr_number)
            return

        # Checkout the existing PR branch
        if not _git_checkout_existing_branch(clone_path, branch_name):
            logger.error("Failed to checkout branch %s for PR #%d", branch_name, pr_number)
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


async def _run_coder_plan_mode(
    *,
    context: AgentContext,
    plan: dict,
) -> None:
    """Internal: Run coder in plan mode with an explicitly provided plan."""
    if context.issue_number is None:
        raise ValueError("issue_number is required in context for plan mode")
    
    client = context.gh_client
    issue_number = context.issue_number
    
    # Check if we're in CI fix mode
    is_ci_fix_mode = os.getenv("CI_FIX_MODE", "").lower() == "true"
    context.is_ci_fix_mode = is_ci_fix_mode
    
    # If in CI fix mode, load CI feedback from PR comments
    if is_ci_fix_mode and context.pr_number:
        pr_comments = client.list_pr_comments(context.pr_number)
        ci_feedback = _load_latest_ci_feedback(pr_comments)
        context.ci_feedback = ci_feedback
        if ci_feedback:
            logger.info("Loaded %d CI feedback items from PR #%d", len(ci_feedback), context.pr_number)
    
    issue = client.get_issue(issue_number)
    
    # Set default iteration values
    context.iteration = context.iteration or 1
    context.max_iterations = context.max_iterations or MAX_DEV_ITERATIONS
    
    # Check if there's an existing branch to update
    if is_ci_fix_mode and context.pr_number:
        pr_info = client.get_pull_request(context.pr_number)
        existing_branch = pr_info.head_ref
        logger.info("CI fix mode: using existing PR branch %s", existing_branch)
    else:
        existing_branch = _find_existing_branch(client, issue_number)
    is_update = existing_branch is not None
    
    # Create temporary directory for clone
    temp_dir = tempfile.mkdtemp(prefix="coder_agent_")
    clone_path = Path(temp_dir) / "repo"

    try:
        clone_url = client.get_clone_url()
        token = os.getenv("GH_TOKEN", "")

        logger.info("Coder Agent working on issue #%d (plan mode)", issue_number)

        if not _clone_repository(clone_url, token, clone_path):
            logger.error("Failed to clone repository for issue #%d", issue_number)
            return

        # Handle branching
        if is_update and existing_branch:
            branch_name = existing_branch
            if not _git_checkout_existing_branch(clone_path, branch_name):
                if is_ci_fix_mode:
                    logger.error("Failed to checkout existing branch %s in CI fix mode", branch_name)
                    return
                random_suffix = secrets.token_hex(4)
                branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
                if not _git_create_branch(clone_path, branch_name):
                    logger.error("Failed to create branch %s", branch_name)
                    return
                is_update = False
        else:
            random_suffix = secrets.token_hex(4)
            branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
            if not _git_create_branch(clone_path, branch_name):
                logger.error("Failed to create branch %s", branch_name)
                return

        # Set up context for tools
        context.workspace = clone_path
        index = CodeIndex(str(clone_path))
        index.build()
        context.index = index

        # Run the agent
        summary = await run_coder_agent_async(issue, plan, context)

        # Commit and push changes
        commit_prefix = "fix" if is_update else "feat"
        commit_message = f"{commit_prefix}: implement changes for #{issue.number}\n\n{summary}"
        has_changes = _git_commit(clone_path, commit_message)

        if has_changes:
            if _git_push(clone_path, branch_name):
                logger.info("Pushed changes to branch %s", branch_name)
            else:
                logger.error("Failed to push changes to branch %s", branch_name)
        else:
            logger.info("No changes to commit for issue #%d", issue_number)

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def run_coder_unified(
    *,
    context: AgentContext,
    plan: dict | None = None,
    pr_comment_history: list[dict] | None = None,
) -> None:
    """Synchronous wrapper for run_coder_unified_async."""
    asyncio.run(run_coder_unified_async(
        context=context,
        plan=plan,
        pr_comment_history=pr_comment_history,
    ))

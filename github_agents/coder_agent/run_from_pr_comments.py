"""PR comments-based entrypoint for the Coder Agent.

This module runs the coder agent based on comment history on an existing PR.
It commits directly into the existing PR branch.
"""

from __future__ import annotations

import asyncio
import logging
import os

from github_agents.coder_agent.agent import (
    MAX_DEV_ITERATIONS,
    _clone_repository,
    _git_commit,
    _git_push,
    run_coder_agent_from_pr_comments_async,
)
from github_agents.coder_agent.runner_utils import (
    get_clone_token,
    load_ci_feedback_from_pr,
    load_comment_history_from_pr,
    setup_ci_fix_mode,
    setup_context_for_workspace,
    temp_clone_directory,
)
from github_agents.common.config import load_config
from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk

logger = logging.getLogger(__name__)



async def run_coder_from_pr_async(*, context: AgentContext) -> None:
    """Run the coder agent based on PR comment history.

    This loads comment history from the PR and runs the coder agent
    to address feedback. Commits directly into the existing PR branch.

    Args:
        context: Agent context with pr_number set.
    """
    if context.pr_number is None:
        raise ValueError("pr_number is required in context for PR comments mode")

    client = context.gh_client
    pr_number = context.pr_number

    # Check and configure CI fix mode
    setup_ci_fix_mode(context)

    # Get PR details
    pr_info = client.get_pull_request(pr_number)
    branch_name = pr_info.head_ref

    # Load comment history from PR
    comment_history = load_comment_history_from_pr(client, pr_number)

    # If in CI fix mode, also load CI feedback
    is_ci_fix_mode = os.getenv("CI_FIX_MODE", "").lower() == "true"
    if is_ci_fix_mode:
        context.is_ci_fix_mode = True
        load_ci_feedback_from_pr(context, client, pr_number)

    # Set default iteration values
    context.iteration = context.iteration or 1
    context.max_iterations = context.max_iterations or MAX_DEV_ITERATIONS

    with temp_clone_directory(prefix="coder_agent_pr_") as clone_path:
        clone_url = client.get_clone_url()
        token = get_clone_token()

        logger.info(
            "Coder Agent working on PR #%d, branch %s (PR comments mode)", pr_number, branch_name
        )

        # Clone directly with the PR branch
        if not _clone_repository(clone_url, token, clone_path, branch=branch_name):
            logger.error("Failed to clone repository for PR #%d", pr_number)
            return

        # Set up context for tools
        setup_context_for_workspace(context, clone_path)

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


def run_coder_from_pr(*, context: AgentContext) -> None:
    """Run the coder agent based on PR comment history.

    Synchronous wrapper for run_coder_from_pr_async.

    Args:
        context: Agent context with pr_number set.
    """
    asyncio.run(run_coder_from_pr_async(context=context))


def main() -> int:
    """CLI entry point for the PR comments-based coder agent.

    Environment variables:
        PR_NUMBER: Required. The PR number to work on.
        CI_FIX_MODE: Optional. Set to "true" to also load CI feedback.
    """
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
        raise ValueError(f"PR_NUMBER must be an integer, got: {pr_number_str}") from None

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

"""Plan-based entrypoint for the Coder Agent.

This module runs the coder agent based on a plan extracted from issue comments.
It creates a new branch (or updates an existing one) and creates/updates a PR.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from github_agents.coder_agent.agent import (
    MAX_DEV_ITERATIONS,
    _clone_repository,
    _get_iteration_count,
    _git_commit,
    _git_create_branch,
    _git_push,
    _update_iteration_count,
    run_coder_agent_async,
)
from github_agents.coder_agent.messages import (
    comment_branch_creation_failed,
    comment_clone_failed,
    comment_max_iterations_reached,
    comment_no_changes,
    comment_no_plan_found,
    comment_pr_created,
    comment_pr_creation_failed,
    comment_push_failed,
    comment_push_success,
    comment_starting_implementation,
)
from github_agents.coder_agent.runner_utils import (
    determine_branch_for_issue,
    generate_new_branch_name,
    get_clone_token,
    load_plan_from_issue,
    setup_ci_fix_mode,
    setup_context_for_workspace,
    temp_clone_directory,
)
from github_agents.common.config import get_issue_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


async def run_coder_async(*, context: AgentContext) -> None:
    """Run the coder agent based on a plan from issue comments.

    This is the main async entrypoint for plan-based coding.
    The plan will be loaded from the issue comments automatically.

    Args:
        context: Agent context with issue_number set.
    """
    if context.issue_number is None:
        raise ValueError("issue_number is required in context for plan mode")

    client = context.gh_client
    issue_number = context.issue_number

    # Check and configure CI fix mode
    setup_ci_fix_mode(context)

    issue = client.get_issue(issue_number)

    # Load plan from issue comments
    plan = load_plan_from_issue(client, issue_number)

    if not plan:
        comment_no_plan_found(client, issue.number, issue.url)
        return

    # Get and update iteration count
    current_iteration = _get_iteration_count(client, issue_number)
    new_iteration = current_iteration + 1

    if new_iteration > MAX_DEV_ITERATIONS:
        comment_max_iterations_reached(
            client, issue.number, issue.url, (current_iteration, MAX_DEV_ITERATIONS)
        )
        return

    _update_iteration_count(client, issue_number, new_iteration)
    logger.info(
        "Starting iteration %d/%d for issue #%d", new_iteration, MAX_DEV_ITERATIONS, issue_number
    )

    # Update context with iteration info
    context.iteration = new_iteration
    context.max_iterations = MAX_DEV_ITERATIONS

    # Determine which branch to use
    branch_info = determine_branch_for_issue(
        client,
        issue_number,
        is_ci_fix_mode=context.is_ci_fix_mode,
        pr_number=context.pr_number,
    )
    is_update = branch_info.is_update

    with temp_clone_directory(prefix="coder_agent_") as clone_path:
        clone_url = client.get_clone_url()
        token = get_clone_token()

        comment_starting_implementation(
            client,
            issue.number,
            (new_iteration, MAX_DEV_ITERATIONS),
            is_update=is_update,
            branch=branch_info.branch_name if is_update else None,
        )

        # Handle branching - clone with the appropriate branch
        branch_name = branch_info.branch_name
        if is_update:
            if not _clone_repository(clone_url, token, clone_path, branch=branch_name):
                if context.is_ci_fix_mode:
                    comment_clone_failed(
                        client, issue.number, branch=branch_name, is_ci_fix_mode=True
                    )
                    logger.error("Failed to clone existing branch %s in CI fix mode", branch_name)
                    return
                # Normal mode: fall back to cloning default branch and creating new branch
                if not _clone_repository(clone_url, token, clone_path):
                    comment_clone_failed(client, issue.number)
                    logger.error("Failed to clone repository for issue #%d", issue_number)
                    return
                branch_name = generate_new_branch_name(issue.number)
                if not _git_create_branch(clone_path, branch_name):
                    comment_branch_creation_failed(client, issue.number, branch_name)
                    logger.error("Failed to create branch %s", branch_name)
                    return
                is_update = False
        else:
            if not _clone_repository(clone_url, token, clone_path):
                comment_clone_failed(client, issue.number)
                logger.error("Failed to clone repository for issue #%d", issue_number)
                return
            if not _git_create_branch(clone_path, branch_name):
                comment_branch_creation_failed(client, issue.number, branch_name)
                logger.error("Failed to create branch %s", branch_name)
                return

        # Set up context for tools
        setup_context_for_workspace(context, clone_path)

        # Run the agent
        summary = await run_coder_agent_async(issue, plan, context)

        # Commit and push changes
        commit_prefix = "fix" if is_update else "feat"
        commit_message = (
            f"{commit_prefix}: implement changes for #{issue.number} "
            f"(iteration {new_iteration})\n\n{summary}"
        )
        has_changes = _git_commit(clone_path, commit_message)

        if has_changes:
            if _git_push(clone_path, branch_name):
                if is_update:
                    comment_push_success(
                        client,
                        issue.number,
                        issue_url=issue.url,
                        branch=branch_name,
                        iteration=(new_iteration, MAX_DEV_ITERATIONS),
                        summary=summary,
                        is_update=True,
                    )
                    logger.info("Pushed updates to branch %s", branch_name)
                else:
                    # Create PR for new branch
                    pr_title = f"[Coder Agent] {issue.title}"
                    pr_body = f"""## Summary

This PR was automatically generated by the Coder Agent to address #{issue.number}.

**Iteration:** {new_iteration}/{MAX_DEV_ITERATIONS}

### Implementation Summary
{summary}

### Plan Followed
```json
{json.dumps(plan, indent=2)}
```

---
*Generated by Coder Agent*
"""
                    try:
                        pr = client.create_pull_request(
                            title=pr_title,
                            body=pr_body,
                            head=branch_name,
                        )
                        comment_pr_created(
                            client,
                            issue.number,
                            issue_url=issue.url,
                            pr_url=pr.url,
                            iteration=(new_iteration, MAX_DEV_ITERATIONS),
                            summary=summary,
                        )
                        logger.info("Created PR %s for issue #%d", pr.url, issue_number)
                    except Exception as exc:
                        logger.exception("Failed to create PR: %s", exc)
                        comment_pr_creation_failed(client, issue.number, branch_name, exc)
            else:
                comment_push_failed(client, issue.number, branch_name)
                logger.error("Failed to push changes to branch %s", branch_name)
        else:
            comment_no_changes(
                client,
                issue.number,
                issue_url=issue.url,
                iteration=(new_iteration, MAX_DEV_ITERATIONS),
                summary=summary,
            )
            logger.info("No changes to commit for issue #%d", issue_number)


def run_coder(*, context: AgentContext) -> None:
    """Run the coder agent based on a plan from issue comments.

    Synchronous wrapper for run_coder_async.

    Args:
        context: Agent context with issue_number set.
    """
    asyncio.run(run_coder_async(context=context))


# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------


def main() -> int:
    """CLI entry point for the plan-based coder agent.

    Environment variables:
        ISSUE_NUMBER: Required. The issue number to implement.
        PR_NUMBER: Optional. PR number for CI fix mode.
        CI_FIX_MODE: Optional. Set to "true" to enable CI fix mode.
    """
    cfg = load_config()
    issue_number = get_issue_number()

    # Configure the SDK for OpenRouter
    configure_sdk()

    # Get PR number if available (used in CI fix mode)
    pr_number = None
    pr_number_str = os.getenv("PR_NUMBER", "")
    if pr_number_str:
        try:
            pr_number = int(pr_number_str)
        except ValueError:
            pass

    # Create context
    context = AgentContext(
        gh_client=cfg.gh_client,
        model=cfg.model,
        issue_number=issue_number,
        pr_number=pr_number,
    )

    run_coder(context=context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

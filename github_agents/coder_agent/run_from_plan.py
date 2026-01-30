"""Plan-based entrypoint for the Coder Agent.

This entrypoint runs the coder agent based on a plan extracted from issue comments.
It creates a new branch (or updates an existing one) and creates/updates a PR.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from github_agents.common.code_index import CodeIndex
from github_agents.common.config import get_issue_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk

from github_agents.coder_agent.agent import (
    MAX_DEV_ITERATIONS,
    _clone_repository,
    _find_existing_branch,
    _get_iteration_count,
    _git_commit,
    _git_create_branch,
    _git_push,
    _load_latest_ci_feedback,
    _load_latest_plan,
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

logger = logging.getLogger(__name__)


async def run_coder_async(*, context: AgentContext) -> None:
    """Main entry point for the coder agent using a plan from issue comments."""
    if context.issue_number is None:
        raise ValueError("issue_number is required in context")
    
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
    comments = client.list_issue_comments(issue_number)
    plan = _load_latest_plan(comments)

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
    logger.info("Starting iteration %d/%d for issue #%d", new_iteration, MAX_DEV_ITERATIONS, issue_number)

    # Update context with iteration info
    context.iteration = new_iteration
    context.max_iterations = MAX_DEV_ITERATIONS

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

        comment_starting_implementation(
            client,
            issue.number,
            (new_iteration, MAX_DEV_ITERATIONS),
            is_update=is_update,
            branch=existing_branch,
        )

        # Handle branching - clone with the appropriate branch
        if is_update and existing_branch:
            branch_name = existing_branch
            # Clone directly with the existing branch
            if not _clone_repository(clone_url, token, clone_path, branch=branch_name):
                # In CI fix mode, don't fall back to creating a new branch
                if is_ci_fix_mode:
                    comment_clone_failed(
                        client, issue.number, branch=branch_name, is_ci_fix_mode=True
                    )
                    return
                # Normal mode: fall back to cloning default branch and creating new branch
                if not _clone_repository(clone_url, token, clone_path):
                    comment_clone_failed(client, issue.number)
                    return
                random_suffix = secrets.token_hex(4)
                branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
                if not _git_create_branch(clone_path, branch_name):
                    comment_branch_creation_failed(client, issue.number, branch_name)
                    return
                is_update = False
        else:
            if not _clone_repository(clone_url, token, clone_path):
                comment_clone_failed(client, issue.number)
                return
            random_suffix = secrets.token_hex(4)
            branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
            if not _git_create_branch(clone_path, branch_name):
                comment_branch_creation_failed(client, issue.number, branch_name)
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
        commit_message = f"{commit_prefix}: implement changes for #{issue.number} (iteration {new_iteration})\n\n{summary}"
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
                else:
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
                    except Exception as exc:
                        logger.exception("Failed to create PR: %s", exc)
                        comment_pr_creation_failed(client, issue.number, branch_name, exc)
            else:
                comment_push_failed(client, issue.number, branch_name)
        else:
            comment_no_changes(
                client,
                issue.number,
                issue_url=issue.url,
                iteration=(new_iteration, MAX_DEV_ITERATIONS),
                summary=summary,
            )

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def run_coder(*, context: AgentContext) -> None:
    """Synchronous wrapper for run_coder_async."""
    asyncio.run(run_coder_async(context=context))


def main() -> int:
    """CLI entry point for the plan-based coder agent."""
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

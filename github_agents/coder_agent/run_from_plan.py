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
    _git_checkout_existing_branch,
    _git_commit,
    _git_create_branch,
    _git_push,
    _load_latest_ci_feedback,
    _load_latest_plan,
    _update_iteration_count,
    run_coder_agent_async,
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
        client.comment_issue(
            issue.number,
            "\n".join([
                "🧩 **Coder Agent could not find a plan.**",
                "",
                f"- Issue: {issue.url}",
                "Please ensure the Planner Agent has created a plan first.",
            ]),
        )
        return

    # Get and update iteration count
    current_iteration = _get_iteration_count(client, issue_number)
    new_iteration = current_iteration + 1
    
    if new_iteration > MAX_DEV_ITERATIONS:
        client.comment_issue(
            issue.number,
            "\n".join([
                "🧩 **Coder Agent: Maximum iterations reached.**",
                "",
                f"- Issue: {issue.url}",
                f"- Iterations: {current_iteration}/{MAX_DEV_ITERATIONS}",
                "",
                "The maximum number of development iterations has been reached.",
                "Please review the existing PR manually or close the issue.",
            ]),
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

        iteration_msg = f" (iteration {new_iteration}/{MAX_DEV_ITERATIONS})"
        if is_update:
            client.comment_issue(
                issue.number,
                f"🧩 **Coder Agent continuing implementation{iteration_msg}...**\n\n"
                f"Updating existing branch `{existing_branch}`...",
            )
        else:
            client.comment_issue(
                issue.number,
                f"🧩 **Coder Agent starting implementation{iteration_msg}...**\n\nCloning repository...",
            )

        if not _clone_repository(clone_url, token, clone_path):
            client.comment_issue(
                issue.number,
                "🧩 **Coder Agent failed to clone repository.**\n\nPlease check the logs.",
            )
            return

        # Handle branching
        if is_update and existing_branch:
            branch_name = existing_branch
            if not _git_checkout_existing_branch(clone_path, branch_name):
                # In CI fix mode, don't fall back to creating a new branch
                if is_ci_fix_mode:
                    client.comment_issue(
                        issue.number,
                        f"🧩 **Coder Agent failed to checkout existing branch `{branch_name}`.**\n\n"
                        "Cannot proceed with CI fix mode without the existing PR branch.",
                    )
                    return
                # Normal mode: fall back to creating a new branch
                random_suffix = secrets.token_hex(4)
                branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
                if not _git_create_branch(clone_path, branch_name):
                    client.comment_issue(
                        issue.number,
                        f"🧩 **Coder Agent failed to create branch `{branch_name}`.**",
                    )
                    return
                is_update = False
        else:
            random_suffix = secrets.token_hex(4)
            branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
            if not _git_create_branch(clone_path, branch_name):
                client.comment_issue(
                    issue.number,
                    f"🧩 **Coder Agent failed to create branch `{branch_name}`.**",
                )
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
                    client.comment_issue(
                        issue.number,
                        "\n".join([
                            f"🧩 **Coder Agent pushed fixes (iteration {new_iteration}/{MAX_DEV_ITERATIONS}).**",
                            "",
                            f"- Issue: {issue.url}",
                            f"- Branch: `{branch_name}`",
                            "",
                            "### Changes Made",
                            summary,
                            "",
                            "The PR has been updated. Reviewer will analyze the changes.",
                        ]),
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
                        client.comment_issue(
                            issue.number,
                            "\n".join([
                                f"🧩 **Coder Agent completed implementation (iteration {new_iteration}/{MAX_DEV_ITERATIONS}).**",
                                "",
                                f"- Issue: {issue.url}",
                                f"- Pull Request: {pr.url}",
                                "",
                                "### Summary",
                                summary,
                            ]),
                        )
                    except Exception as exc:
                        logger.exception("Failed to create PR: %s", exc)
                        client.comment_issue(
                            issue.number,
                            f"🧩 **Coder Agent pushed changes but failed to create PR.**\n\nBranch: `{branch_name}`\nError: {exc}",
                        )
            else:
                client.comment_issue(
                    issue.number,
                    f"🧩 **Coder Agent failed to push changes.**\n\nBranch: `{branch_name}`",
                )
        else:
            client.comment_issue(
                issue.number,
                "\n".join([
                    f"🧩 **Coder Agent completed but made no changes (iteration {new_iteration}/{MAX_DEV_ITERATIONS}).**",
                    "",
                    f"- Issue: {issue.url}",
                    "",
                    "### Summary",
                    summary,
                ]),
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

"""Coder Agent using OpenAI Agents SDK."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from agents import Agent, Runner
from agents.agent import StopAtTools

from github_agents.common.code_index import CodeIndex
from github_agents.common.config import get_issue_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.github_client import GitHubClient, IssueCommentData, IssueData
from github_agents.common.sdk_config import configure_sdk, get_model_name
from github_agents.common.tools import get_coder_tools
from github_agents.planner_agent.agent import PLAN_MARKER
from github_agents.coder_agent.prompts import build_coder_instructions

logger = logging.getLogger(__name__)

# Max iterations for the agent loop (LLM calls)
MAX_AGENT_ITERATIONS = 50

# Max development iterations (plan -> code -> review cycle)
MAX_DEV_ITERATIONS = 5

# Label prefix for tracking iterations
ITERATION_LABEL_PREFIX = "iteration-"

# CI Fixer agent marker for extracting CI feedback
CI_FIXER_MARKER = "<!-- ci-fixer-agent-report -->"


# --- Plan Extraction ---

def _extract_plan(comment: IssueCommentData) -> dict | None:
    """Extract plan JSON from a planner agent comment."""
    if PLAN_MARKER not in comment.body:
        return None
    match = re.search(r"```json\s*(\{.*?\})\s*```", comment.body, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _load_latest_plan(comments: list[IssueCommentData]) -> dict | None:
    """Find the most recent plan from issue comments."""
    for comment in sorted(comments, key=lambda c: c.created_at, reverse=True):
        parsed = _extract_plan(comment)
        if parsed:
            return parsed
    return None


# --- CI Feedback Extraction ---

def _extract_ci_feedback(comment: IssueCommentData) -> dict | None:
    """Extract CI feedback from a CI fixer agent comment."""
    if CI_FIXER_MARKER not in comment.body:
        return None
    # Extract the machine-readable JSON block
    match = re.search(r"```json\s*(\{.*?\})\s*```", comment.body, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_ci_suggestions(comment_body: str) -> list[str]:
    """Extract suggested fixes from CI fixer comment body."""
    suggestions = []
    
    # Look for the "Suggested Fixes" section
    if "### Suggested Fixes" in comment_body:
        fixes_section = comment_body.split("### Suggested Fixes")[1]
        # Stop at the next section
        if "###" in fixes_section:
            fixes_section = fixes_section.split("###")[0]
        
        # Extract each fix (lines starting with **)
        for line in fixes_section.split("\n"):
            line = line.strip()
            if line.startswith("**") or line.startswith("- "):
                suggestions.append(line.lstrip("*- ").strip())
    
    # Also look for root causes
    if "### Root Causes" in comment_body:
        causes_section = comment_body.split("### Root Causes")[1]
        if "###" in causes_section:
            causes_section = causes_section.split("###")[0]
        
        for line in causes_section.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                suggestions.append(f"Root cause: {line[2:]}")
    
    return suggestions


def _load_latest_ci_feedback(comments: list[IssueCommentData]) -> list[str]:
    """Find the most recent CI fixer feedback from PR comments."""
    for comment in sorted(comments, key=lambda c: c.created_at, reverse=True):
        if CI_FIXER_MARKER in comment.body:
            suggestions = _extract_ci_suggestions(comment.body)
            if suggestions:
                return suggestions
    return []


# --- Iteration Tracking ---

def _get_iteration_count(client: GitHubClient, issue_number: int) -> int:
    """Get current iteration count from issue labels."""
    labels = client.get_issue_labels(issue_number)
    for label in labels:
        if label.startswith(ITERATION_LABEL_PREFIX):
            try:
                return int(label.split("-")[1])
            except (ValueError, IndexError):
                pass
    return 0


def _update_iteration_count(client: GitHubClient, issue_number: int, new_count: int) -> None:
    """Update the iteration count label on an issue."""
    labels = client.get_issue_labels(issue_number)
    
    for label in labels:
        if label.startswith(ITERATION_LABEL_PREFIX):
            client.remove_issue_label(issue_number, label)
    
    new_label = f"{ITERATION_LABEL_PREFIX}{new_count}"
    client.add_issue_label(issue_number, new_label)
    logger.info("Updated iteration label to %s for issue #%d", new_label, issue_number)


def _find_existing_branch(client: GitHubClient, issue_number: int) -> str | None:
    """Find existing coder-agent branch for this issue."""
    prs = client.list_pull_requests(state="open")
    for pr in prs:
        if pr.head_ref.startswith(f"coder-agent/issue-{issue_number}-"):
            return pr.head_ref
    return None


# --- Git Operations ---

def _clone_repository(clone_url: str, token: str, dest: Path) -> bool:
    """Clone the repository to dest directory."""
    if clone_url.startswith("https://"):
        authed_url = clone_url.replace("https://", f"https://x-access-token:{token}@")
    else:
        authed_url = clone_url

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", authed_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Clone failed: %s", exc.stderr)
        return False


def _git_create_branch(workdir: Path, branch_name: str) -> bool:
    """Create and checkout a new branch."""
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Branch creation failed: %s", exc.stderr)
        return False


def _git_checkout_existing_branch(workdir: Path, branch_name: str) -> bool:
    """Checkout an existing remote branch."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch_name],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to checkout existing branch: %s", exc.stderr)
        return False


def _git_commit(workdir: Path, message: str) -> bool:
    """Stage all changes and commit."""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            logger.info("No changes to commit")
            return False

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "Coder Agent",
                "GIT_AUTHOR_EMAIL": "agent@example.com",
                "GIT_COMMITTER_NAME": "Coder Agent",
                "GIT_COMMITTER_EMAIL": "agent@example.com",
            },
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Commit failed: %s", exc.stderr)
        return False


def _git_push(workdir: Path, branch_name: str) -> bool:
    """Push branch to origin."""
    try:
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("Push failed: %s", exc.stderr)
        return False


# --- Agent Definition ---

def _build_coder_agent(
    issue: IssueData,
    plan: dict,
    context: AgentContext,
) -> Agent[AgentContext]:
    """Build the coder agent with dynamic instructions."""
    instructions = build_coder_instructions(
        issue_title=issue.title,
        issue_body=issue.body,
        plan_summary=plan.get('summary', 'No summary'),
        steps=plan.get("steps", []),
        iteration=context.iteration,
        max_iterations=context.max_iterations,
        reviewer_feedback=context.reviewer_feedback,
        ci_feedback=context.ci_feedback,
        is_ci_fix_mode=context.is_ci_fix_mode,
    )
    
    return Agent[AgentContext](
        name="Coder",
        model=get_model_name(),
        instructions=instructions,
        tools=get_coder_tools(),
        tool_use_behavior=StopAtTools(stop_at_tool_names=["mark_complete"]),
    )


# --- Agent Execution ---

async def run_coder_agent_async(
    issue: IssueData,
    plan: dict,
    context: AgentContext,
) -> str:
    """Run the coder agent and return the completion summary."""
    agent = _build_coder_agent(issue, plan, context)
    
    try:
        result = await Runner.run(
            agent,
            "Please implement the changes according to the plan. Start by exploring the codebase structure.",
            context=context,
            max_turns=MAX_AGENT_ITERATIONS,
        )
        
        # Extract the summary from the mark_complete tool output
        output = str(result.final_output or "")
        if output.startswith("COMPLETE: "):
            return output[10:]
        return output or "Agent completed without explicit summary."
        
    except Exception as exc:
        logger.exception("Coder agent failed: %s", exc)
        return f"Agent execution failed: {exc}"


async def run_coder_async(*, context: AgentContext) -> None:
    """Main entry point for the coder agent (async version)."""
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
    """CLI entry point for the coder agent."""
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

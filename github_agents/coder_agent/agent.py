"""Coder Agent core logic using OpenAI Agents SDK.

This module contains the core agent building, execution, and helper functions.
For entrypoints, see:
- run_from_plan.py: Plan-based entrypoint (loads plan from issue comments)
- run_from_pr_comments.py: PR comments-based entrypoint (commits into existing PR)
- run_unified.py: Unified entrypoint that can handle both modes
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from agents import Agent, Runner
from agents.agent import StopAtTools

from github_agents.coder_agent.prompts import (
    build_coder_instructions,
    build_coder_pr_comments_instructions,
)
from github_agents.common.context import AgentContext
from github_agents.common.github_client import GitHubClient, IssueCommentData, IssueData
from github_agents.common.sdk_config import get_model_name
from github_agents.common.tools import get_coder_tools
from github_agents.planner_agent.agent import PLAN_MARKER

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


def _clone_repository(clone_url: str, token: str, dest: Path, *, branch: str | None = None) -> bool:
    """Clone the repository to dest directory.

    Args:
        clone_url: The repository clone URL.
        token: GitHub access token.
        dest: Destination directory.
        branch: Optional branch to clone. If provided, clones that specific branch.
    """
    if clone_url.startswith("https://"):
        authed_url = clone_url.replace("https://", f"https://x-access-token:{token}@")
    else:
        authed_url = clone_url

    try:
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([authed_url, str(dest)])

        subprocess.run(
            cmd,
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
        plan_summary=plan.get("summary", "No summary"),
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


def _build_coder_agent_from_pr_comments(
    pr_title: str,
    pr_body: str,
    branch_name: str,
    comment_history: list[dict],
    context: AgentContext,
) -> Agent[AgentContext]:
    """Build the coder agent with instructions based on PR comment history."""
    instructions = build_coder_pr_comments_instructions(
        pr_title=pr_title,
        pr_body=pr_body,
        branch_name=branch_name,
        comment_history=comment_history,
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


async def run_coder_agent_from_pr_comments_async(
    pr_title: str,
    pr_body: str,
    branch_name: str,
    comment_history: list[dict],
    context: AgentContext,
) -> str:
    """Run the coder agent based on PR comment history and return the completion summary."""
    agent = _build_coder_agent_from_pr_comments(
        pr_title=pr_title,
        pr_body=pr_body,
        branch_name=branch_name,
        comment_history=comment_history,
        context=context,
    )

    try:
        result = await Runner.run(
            agent,
            "Please address the feedback from the PR comments. Start by exploring the codebase to understand the current state.",
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

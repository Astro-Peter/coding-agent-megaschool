"""Message formatting helpers for the Coder Agent.

This module provides consistent formatting for GitHub issue/PR comments
posted by the coder agent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from github_agents.common.github_client import GitHubClient


def format_agent_message(
    header: str,
    *,
    issue_url: str | None = None,
    pr_url: str | None = None,
    branch: str | None = None,
    iteration: tuple[int, int] | None = None,
    summary: str | None = None,
    summary_header: str = "Summary",
    extra_lines: list[str] | None = None,
) -> str:
    """Format a standard coder agent message with consistent structure.
    
    Args:
        header: The main message header (without emoji prefix).
        issue_url: Optional issue URL to include.
        pr_url: Optional PR URL to include.
        branch: Optional branch name to include.
        iteration: Optional (current, max) iteration tuple.
        summary: Optional summary text to include in a section.
        summary_header: Header for the summary section (default: "Summary").
        extra_lines: Optional additional lines to append.
    
    Returns:
        Formatted markdown message string.
    """
    lines = [f"🧩 **{header}**", ""]
    
    # Add metadata bullets
    has_metadata = False
    if issue_url:
        lines.append(f"- Issue: {issue_url}")
        has_metadata = True
    if pr_url:
        lines.append(f"- Pull Request: {pr_url}")
        has_metadata = True
    if branch:
        lines.append(f"- Branch: `{branch}`")
        has_metadata = True
    if iteration:
        lines.append(f"- Iterations: {iteration[0]}/{iteration[1]}")
        has_metadata = True
    
    # Add summary section
    if summary:
        if has_metadata:
            lines.append("")
        lines.extend([f"### {summary_header}", summary])
    
    # Add extra lines if provided
    if extra_lines:
        if has_metadata or summary:
            lines.append("")
        lines.extend(extra_lines)
    
    return "\n".join(lines)


def comment_agent_status(
    client: GitHubClient,
    issue_number: int,
    header: str,
    **kwargs,
) -> None:
    """Post a formatted agent status message to an issue.
    
    Args:
        client: The GitHub client instance.
        issue_number: The issue number to comment on.
        header: The main message header.
        **kwargs: Additional arguments passed to format_agent_message.
    """
    message = format_agent_message(header, **kwargs)
    client.comment_issue(issue_number, message)


def comment_no_plan_found(client: GitHubClient, issue_number: int, issue_url: str) -> None:
    """Post a message indicating no plan was found."""
    comment_agent_status(
        client,
        issue_number,
        "Coder Agent could not find a plan.",
        issue_url=issue_url,
        extra_lines=["Please ensure the Planner Agent has created a plan first."],
    )


def comment_max_iterations_reached(
    client: GitHubClient,
    issue_number: int,
    issue_url: str,
    iteration: tuple[int, int],
) -> None:
    """Post a message indicating max iterations have been reached."""
    comment_agent_status(
        client,
        issue_number,
        "Coder Agent: Maximum iterations reached.",
        issue_url=issue_url,
        iteration=iteration,
        extra_lines=[
            "The maximum number of development iterations has been reached.",
            "Please review the existing PR manually or close the issue.",
        ],
    )


def comment_starting_implementation(
    client: GitHubClient,
    issue_number: int,
    iteration: tuple[int, int],
    *,
    is_update: bool = False,
    branch: str | None = None,
) -> None:
    """Post a message indicating implementation is starting."""
    iteration_msg = f" (iteration {iteration[0]}/{iteration[1]})"
    if is_update:
        header = f"Coder Agent continuing implementation{iteration_msg}..."
        extra = [f"Updating existing branch `{branch}`..."] if branch else []
    else:
        header = f"Coder Agent starting implementation{iteration_msg}..."
        extra = ["Cloning repository..."]
    
    comment_agent_status(client, issue_number, header, extra_lines=extra)


def comment_clone_failed(
    client: GitHubClient,
    issue_number: int,
    *,
    branch: str | None = None,
    is_ci_fix_mode: bool = False,
) -> None:
    """Post a message indicating clone failed."""
    if is_ci_fix_mode and branch:
        header = f"Coder Agent failed to clone existing branch `{branch}`."
        extra = ["Cannot proceed with CI fix mode without the existing PR branch."]
    else:
        header = "Coder Agent failed to clone repository."
        extra = ["Please check the logs."]
    
    comment_agent_status(client, issue_number, header, extra_lines=extra)


def comment_branch_creation_failed(
    client: GitHubClient,
    issue_number: int,
    branch: str,
) -> None:
    """Post a message indicating branch creation failed."""
    comment_agent_status(
        client,
        issue_number,
        f"Coder Agent failed to create branch `{branch}`.",
    )


def comment_push_success(
    client: GitHubClient,
    issue_number: int,
    *,
    issue_url: str,
    branch: str,
    iteration: tuple[int, int],
    summary: str,
    is_update: bool = False,
) -> None:
    """Post a message indicating changes were pushed successfully."""
    if is_update:
        comment_agent_status(
            client,
            issue_number,
            f"Coder Agent pushed fixes (iteration {iteration[0]}/{iteration[1]}).",
            issue_url=issue_url,
            branch=branch,
            summary=summary,
            summary_header="Changes Made",
            extra_lines=["The PR has been updated. Reviewer will analyze the changes."],
        )
    else:
        # For new PRs, the caller should handle PR creation separately
        pass


def comment_pr_created(
    client: GitHubClient,
    issue_number: int,
    *,
    issue_url: str,
    pr_url: str,
    iteration: tuple[int, int],
    summary: str,
) -> None:
    """Post a message indicating PR was created successfully."""
    comment_agent_status(
        client,
        issue_number,
        f"Coder Agent completed implementation (iteration {iteration[0]}/{iteration[1]}).",
        issue_url=issue_url,
        pr_url=pr_url,
        summary=summary,
    )


def comment_pr_creation_failed(
    client: GitHubClient,
    issue_number: int,
    branch: str,
    error: Exception,
) -> None:
    """Post a message indicating PR creation failed."""
    comment_agent_status(
        client,
        issue_number,
        "Coder Agent pushed changes but failed to create PR.",
        branch=branch,
        extra_lines=[f"Error: {error}"],
    )


def comment_push_failed(
    client: GitHubClient,
    issue_number: int,
    branch: str,
) -> None:
    """Post a message indicating push failed."""
    comment_agent_status(
        client,
        issue_number,
        "Coder Agent failed to push changes.",
        branch=branch,
    )


def comment_no_changes(
    client: GitHubClient,
    issue_number: int,
    *,
    issue_url: str,
    iteration: tuple[int, int],
    summary: str,
) -> None:
    """Post a message indicating no changes were made."""
    comment_agent_status(
        client,
        issue_number,
        f"Coder Agent completed but made no changes (iteration {iteration[0]}/{iteration[1]}).",
        issue_url=issue_url,
        summary=summary,
    )

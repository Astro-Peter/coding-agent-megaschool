"""Reviewer Agent using OpenAI Agents SDK."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agents import Agent, RunConfig, Runner

from github_agents.common.code_index import CodeIndex
from github_agents.common.config import get_pr_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.github_client import CheckRunData, GitHubClient, IssueData
from github_agents.common.sdk_config import configure_sdk, get_model_name
from github_agents.common.tools import get_reviewer_tools

logger = logging.getLogger(__name__)

# Marker for machine-readable feedback
REVIEWER_FEEDBACK_MARKER = "<!-- reviewer-agent-feedback -->"

# Maximum iterations before forcing approval
MAX_ITERATIONS = 5


class ReviewDecision(BaseModel):
    """Structured output from the reviewer agent."""
    status: Literal["APPROVED", "CHANGES_REQUESTED"] = Field(
        description="The review decision: APPROVED or CHANGES_REQUESTED"
    )
    summary: str = Field(description="Brief overall assessment of the PR")
    issues: list[str] = Field(
        default_factory=list,
        description="List of specific issues found, empty if approved"
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Optional improvements that don't block approval"
    )


class ReviewDecisionWithMeta(BaseModel):
    """Review decision with iteration metadata."""
    status: str
    summary: str
    issues: list[str]
    suggestions: list[str]
    iteration: int
    max_iterations: int


# --- Helper Functions ---

def _extract_issue_number(pr_body: str) -> int | None:
    """Extract linked issue number from PR body."""
    patterns = [
        r"(?:closes|fixes|resolves|addresses|for)\s*#(\d+)",
        r"issue[:\s]*#?(\d+)",
        r"#(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, pr_body, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _get_iteration_count(client: GitHubClient, issue_number: int) -> int:
    """Get current iteration count from issue labels."""
    labels = client.get_issue_labels(issue_number)
    for label in labels:
        if label.startswith("iteration-"):
            try:
                return int(label.split("-")[1])
            except (ValueError, IndexError):
                pass
    return 1


def _format_ci_status(check_runs: list[CheckRunData]) -> str:
    """Format CI check results for the review prompt."""
    if not check_runs:
        return "No CI checks found."
    
    lines = ["CI Status:"]
    all_passed = True
    for check in check_runs:
        status_emoji = "✅" if check.conclusion == "success" else "❌"
        if check.conclusion != "success":
            all_passed = False
        lines.append(f"  {status_emoji} {check.name}: {check.conclusion}")
    
    if all_passed:
        lines.append("\nAll CI checks passed.")
    else:
        lines.append("\nSome CI checks are failing.")
    
    return "\n".join(lines)


def _format_diff_summary(files: list, max_patch_size: int = 5000) -> str:
    """Format the diff for the review prompt, truncating if too large."""
    if not files:
        return "No files changed."
    
    lines = [f"Changed files ({len(files)} total):"]
    total_additions = 0
    total_deletions = 0
    
    for f in files:
        total_additions += f.additions
        total_deletions += f.deletions
        lines.append(f"  - {f.filename} (+{f.additions}/-{f.deletions}) [{f.status}]")
    
    lines.append(f"\nTotal: +{total_additions}/-{total_deletions}")
    lines.append("\n--- Detailed Changes ---\n")
    
    current_size = 0
    for i, f in enumerate(files):
        if f.patch:
            patch_header = f"\n### {f.filename}\n```diff\n{f.patch}\n```\n"
            if current_size + len(patch_header) > max_patch_size:
                lines.append(f"\n(Remaining {len(files) - i} files truncated due to size)")
                break
            lines.append(patch_header)
            current_size += len(patch_header)
    
    return "\n".join(lines)


def _format_review_body(decision: ReviewDecisionWithMeta) -> str:
    """Format the PR review body."""
    lines = [
        f"## AI Reviewer Agent - {decision.status}",
        "",
        f"**Iteration:** {decision.iteration}/{decision.max_iterations}",
        "",
        "### Summary",
        decision.summary,
        "",
    ]
    
    if decision.issues:
        lines.extend([
            "### Issues",
            *[f"- {issue}" for issue in decision.issues],
            "",
        ])
    
    if decision.status == "APPROVED":
        lines.append("This PR is ready for merge.")
    else:
        lines.append("Please address the issues above before this PR can be approved.")
    
    return "\n".join(lines)


def _format_review_comment(decision: ReviewDecisionWithMeta, pr_url: str, branch: str) -> str:
    """Format the review as a GitHub comment with machine-readable section."""
    status_emoji = "✅" if decision.status == "APPROVED" else "🔄"
    
    lines = [
        REVIEWER_FEEDBACK_MARKER,
        f"## {status_emoji} AI Reviewer Agent Report",
        "",
        f"**Status:** `{decision.status}`",
        f"**Iteration:** {decision.iteration}/{decision.max_iterations}",
        f"**PR:** {pr_url}",
        f"**Branch:** `{branch}`",
        "",
        "### Summary",
        decision.summary,
        "",
    ]
    
    if decision.issues:
        lines.extend([
            "### Issues Found",
            *[f"- {issue}" for issue in decision.issues],
            "",
        ])
    
    if decision.status == "CHANGES_REQUESTED":
        lines.extend([
            "---",
            "**Next Steps:** The Coder Agent will automatically attempt to fix these issues.",
            "",
        ])
    else:
        lines.extend([
            "---",
            "**This PR is ready for human review and merge.**",
            "",
        ])
    
    # Add machine-readable data block
    data_block = {
        "status": decision.status,
        "iteration": decision.iteration,
        "max_iterations": decision.max_iterations,
        "issues": decision.issues,
    }
    lines.extend([
        "<details>",
        "<summary>Machine-readable data (for automation)</summary>",
        "",
        "```json",
        json.dumps(data_block, indent=2),
        "```",
        "",
        "</details>",
    ])
    
    return "\n".join(lines)


def _write_actions_summary(decision: ReviewDecisionWithMeta, pr_url: str, branch: str) -> None:
    """Write review summary to GitHub Actions summary file."""
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    
    status_emoji = "✅" if decision.status == "APPROVED" else "🔄"
    
    lines = [
        f"# {status_emoji} AI Reviewer Agent Report",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| **Status** | `{decision.status}` |",
        f"| **Iteration** | {decision.iteration}/{decision.max_iterations} |",
        f"| **PR** | {pr_url} |",
        f"| **Branch** | `{branch}` |",
        "",
        "## Summary",
        "",
        decision.summary,
        "",
    ]
    
    if decision.issues:
        lines.extend([
            "## Issues Found",
            "",
            *[f"- {issue}" for issue in decision.issues],
            "",
        ])
    
    if decision.status == "APPROVED":
        lines.append("**This PR is ready for human review and merge.**")
    else:
        lines.append("**Next Steps:** The Coder Agent will automatically attempt to fix these issues.")
    
    try:
        with open(summary_file, "a") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("Wrote review summary to GitHub Actions summary")
    except Exception as e:
        logger.warning("Failed to write Actions summary: %s", e)


def _write_status_output(status: str) -> None:
    """Write review status as GitHub Actions step output."""
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        try:
            with open(output_file, "a") as f:
                f.write(f"status={status}\n")
        except Exception:
            pass


# --- Agent Definition ---

def _build_reviewer_instructions(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    ci_status: str,
    issue: IssueData | None,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build the instructions for the reviewer agent."""
    issue_context = ""
    if issue:
        issue_context = f"""
## Original Issue Requirements
- Issue #{issue.number}: {issue.title}
- Description: {issue.body}

Compare the implementation against these requirements.
"""
    
    return f"""You are an expert code reviewer. Analyze the pull request and provide a structured review decision.

## Pull Request Review

**Title:** {pr_title}
**Iteration:** {iteration}/{max_iterations}

### PR Description
{pr_body}

{issue_context}

### {ci_status}

### Code Changes
{diff_summary}

## Your Task

Review this pull request and determine if it should be approved or if changes are needed.

Consider:
1. Does the implementation match the issue requirements?
2. Are there any bugs, errors, or code quality issues?
3. Are CI checks passing?
4. Is the code well-structured and maintainable?

If this is iteration {max_iterations}/{max_iterations}, you should approve with warnings rather than requesting more changes.

Use the search_codebase tool if you need additional context from the repository.

Provide your decision with:
- status: "APPROVED" or "CHANGES_REQUESTED"
- summary: Brief overall assessment
- issues: List of specific issues found (empty if approved)
- suggestions: Optional improvements that don't block approval
"""


def _build_reviewer_agent(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    ci_status: str,
    issue: IssueData | None,
    iteration: int,
    max_iterations: int,
) -> Agent[AgentContext]:
    """Build the reviewer agent with dynamic instructions."""
    instructions = _build_reviewer_instructions(
        pr_title=pr_title,
        pr_body=pr_body,
        diff_summary=diff_summary,
        ci_status=ci_status,
        issue=issue,
        iteration=iteration,
        max_iterations=max_iterations,
    )
    
    return Agent[AgentContext](
        name="Reviewer",
        instructions=instructions,
        tools=get_reviewer_tools(),
        output_type=ReviewDecision,
    )


# --- Agent Execution ---

async def run_reviewer_agent_async(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    ci_status: str,
    issue: IssueData | None,
    context: AgentContext,
) -> ReviewDecision:
    """Run the reviewer agent and return the decision."""
    agent = _build_reviewer_agent(
        pr_title=pr_title,
        pr_body=pr_body,
        diff_summary=diff_summary,
        ci_status=ci_status,
        issue=issue,
        iteration=context.iteration,
        max_iterations=context.max_iterations,
    )
    
    try:
        # Use LiteLLM model via RunConfig
        run_config = RunConfig(model=get_model_name())
        result = await Runner.run(
            agent,
            "Please review this pull request and provide your decision.",
            context=context,
            max_turns=10,
            run_config=run_config,
        )
        return result.final_output_as(ReviewDecision)
    except Exception as exc:
        logger.exception("Reviewer agent failed: %s", exc)
        return ReviewDecision(
            status="CHANGES_REQUESTED",
            summary=f"Review failed: {exc}",
            issues=["Could not complete automated review"],
            suggestions=[],
        )


async def run_reviewer_async(*, context: AgentContext) -> ReviewDecisionWithMeta | None:
    """Main entry point for the reviewer agent (async version)."""
    if context.pr_number is None:
        raise ValueError("pr_number is required in context")
    
    client = context.gh_client
    pr_number = context.pr_number
    
    logger.info("Starting review for PR #%d", pr_number)
    
    # Get PR details
    pr = client.get_pull_request(pr_number)
    
    # Get changed files and diff
    try:
        files = client.get_pull_request_files(pr_number)
        diff_summary = _format_diff_summary(files)
    except Exception as e:
        logger.warning("Failed to get PR diff: %s", e)
        diff_summary = "Could not fetch diff."
    
    # Get CI status
    try:
        check_runs = client.get_check_runs(pr_number)
        check_runs = [c for c in check_runs if "reviewer" not in c.name.lower()]
        ci_status = _format_ci_status(check_runs)
    except Exception as e:
        logger.warning("Failed to get CI status: %s", e)
        ci_status = "Could not fetch CI status."
        check_runs = []
    
    # Extract linked issue
    issue_number = _extract_issue_number(pr.body)
    issue: IssueData | None = None
    iteration = 1
    
    if issue_number:
        try:
            issue = client.get_issue(issue_number)
            iteration = _get_iteration_count(client, issue_number)
            logger.info("Linked to issue #%d, iteration %d", issue_number, iteration)
        except Exception as e:
            logger.warning("Failed to get linked issue: %s", e)
    
    # Update context
    context.iteration = iteration
    context.max_iterations = MAX_ITERATIONS
    
    # Build code index if workspace is available
    workspace_root = context.workspace or Path(os.getenv("GITHUB_WORKSPACE", os.getcwd()))
    index = CodeIndex(str(workspace_root))
    index.build()
    context.workspace = workspace_root
    context.index = index
    
    # Check if we should force approval
    force_approve = iteration >= MAX_ITERATIONS
    if force_approve:
        logger.info("Max iterations reached, will force approval")
    
    # Run the reviewer agent
    decision = await run_reviewer_agent_async(
        pr_title=pr.title,
        pr_body=pr.body,
        diff_summary=diff_summary,
        ci_status=ci_status,
        issue=issue,
        context=context,
    )
    
    # Force approval if max iterations reached
    if force_approve and decision.status == "CHANGES_REQUESTED":
        decision = ReviewDecision(
            status="APPROVED",
            summary=f"**Forced approval after {MAX_ITERATIONS} iterations.** Original assessment: {decision.summary}",
            issues=["This PR exceeded the maximum iteration limit and was auto-approved."] + decision.issues,
            suggestions=decision.suggestions,
        )
    
    # Check if CI is failing
    ci_failing = any(
        c.status == "completed" and c.conclusion not in ("success", "skipped", "neutral")
        for c in check_runs
    )
    if ci_failing and decision.status == "APPROVED" and not force_approve:
        decision = ReviewDecision(
            status="CHANGES_REQUESTED",
            summary=decision.summary,
            issues=decision.issues + ["CI checks are failing. Please fix before approval."],
            suggestions=decision.suggestions,
        )
    
    # Create decision with metadata
    decision_with_meta = ReviewDecisionWithMeta(
        status=decision.status,
        summary=decision.summary,
        issues=decision.issues,
        suggestions=decision.suggestions,
        iteration=iteration,
        max_iterations=MAX_ITERATIONS,
    )
    
    # Post the review comment
    comment = _format_review_comment(decision_with_meta, pr.url, pr.head_ref)
    client.comment_pull_request(pr.number, comment)
    
    # Post a proper PR review
    review_body = _format_review_body(decision_with_meta)
    review_event = "APPROVE" if decision.status == "APPROVED" else "REQUEST_CHANGES"
    try:
        client.create_pull_request_review(pr.number, body=review_body, event=review_event)
        logger.info("Posted PR review with event: %s", review_event)
    except Exception as e:
        logger.warning("Failed to post PR review: %s", e)
    
    # Write to GitHub Actions
    _write_actions_summary(decision_with_meta, pr.url, pr.head_ref)
    _write_status_output(decision.status)
    
    logger.info("Review completed: %s", decision.status)
    return decision_with_meta


def run_reviewer(*, context: AgentContext) -> ReviewDecisionWithMeta | None:
    """Synchronous wrapper for run_reviewer_async."""
    return asyncio.run(run_reviewer_async(context=context))


def main() -> int:
    """CLI entry point for the reviewer agent."""
    cfg = load_config()
    pr_number = get_pr_number()
    
    # Configure the SDK for OpenRouter
    configure_sdk()
    
    # Create context
    context = AgentContext(
        gh_client=cfg.gh_client,
        model=cfg.model,
        pr_number=pr_number,
    )
    
    run_reviewer(context=context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

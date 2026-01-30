from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass

from agents.common.code_index import CodeIndex
from agents.common.openai_client import build_client, chat_with_tools
from agents.common.github_client import GitHubClient, IssueData, CheckRunData

logger = logging.getLogger(__name__)

# Marker for machine-readable feedback
REVIEWER_FEEDBACK_MARKER = "<!-- reviewer-agent-feedback -->"

# Maximum iterations before forcing approval
MAX_ITERATIONS = 5


@dataclass
class ReviewDecision:
    status: str  # "APPROVED" or "CHANGES_REQUESTED"
    summary: str
    issues: list[str]
    iteration: int
    max_iterations: int


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}")
        sys.exit(0)
    return value


def _extract_issue_number(pr_body: str) -> int | None:
    """Extract linked issue number from PR body (e.g., #123 or issue-123)."""
    # Look for patterns like "#{number}" or "issue #{number}" or "closes #{number}"
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
    return 1  # First iteration


def _format_ci_status(check_runs: list[CheckRunData]) -> str:
    """Format CI check results for the review prompt."""
    if not check_runs:
        return "No CI checks found."
    
    lines = ["CI Status:"]
    all_passed = True
    for check in check_runs:
        status_emoji = "⏳" if check.status != "completed" else (
            "✅" if check.conclusion == "success" else "❌"
        )
        if check.conclusion not in (None, "success"):
            all_passed = False
        lines.append(f"  {status_emoji} {check.name}: {check.status} ({check.conclusion or 'pending'})")
    
    if all_passed and all(c.status == "completed" for c in check_runs):
        lines.append("\nAll CI checks passed.")
    else:
        lines.append("\nSome CI checks are failing or pending.")
    
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
    for f in files:
        if f.patch:
            patch_header = f"\n### {f.filename}\n```diff\n{f.patch}\n```\n"
            if current_size + len(patch_header) > max_patch_size:
                lines.append(f"\n(Remaining {len(files) - files.index(f)} files truncated due to size)")
                break
            lines.append(patch_header)
            current_size += len(patch_header)
    
    return "\n".join(lines)


def _build_review_prompt(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    ci_status: str,
    issue: IssueData | None,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build the comprehensive review prompt."""
    issue_context = ""
    if issue:
        issue_context = f"""
## Original Issue Requirements
- Issue #{issue.number}: {issue.title}
- Description: {issue.body}

Compare the implementation against these requirements.
"""
    
    return f"""## Pull Request Review

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

Respond with a JSON object:
```json
{{
  "status": "APPROVED" or "CHANGES_REQUESTED",
  "summary": "Brief overall assessment",
  "issues": ["List of specific issues found, empty if approved"],
  "suggestions": ["Optional improvements that don't block approval"]
}}
```
"""


def _parse_review_response(response: str) -> dict:
    """Parse the LLM response to extract the review decision."""
    # Try to extract JSON from the response
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try direct JSON parse
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass
    
    # Fallback: try to infer from text
    response_lower = response.lower()
    if "approved" in response_lower and "changes_requested" not in response_lower:
        return {
            "status": "APPROVED",
            "summary": response[:500],
            "issues": [],
            "suggestions": [],
        }
    
    return {
        "status": "CHANGES_REQUESTED",
        "summary": response[:500],
        "issues": ["Could not parse structured response"],
        "suggestions": [],
    }


def _format_review_body(decision: ReviewDecision) -> str:
    """Format the PR review body (for the formal review, not the comment)."""
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


def _write_actions_summary(decision: ReviewDecision, pr_url: str, branch: str) -> None:
    """Write review summary to GitHub Actions summary file."""
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        logger.debug("GITHUB_STEP_SUMMARY not set, skipping Actions summary")
        return
    
    status_emoji = "✅" if decision.status == "APPROVED" else "🔄"
    
    lines = [
        f"# {status_emoji} AI Reviewer Agent Report",
        "",
        f"| Property | Value |",
        f"|----------|-------|",
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


def _format_review_comment(decision: ReviewDecision, pr_url: str, branch: str) -> str:
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


def run_reviewer(
    *,
    client: GitHubClient,
    pr_number: int,
    openai_client,
    model: str,
    workspace: str | None = None,
) -> ReviewDecision | None:
    """Main entry point for the reviewer agent."""
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
        # Filter out this reviewer job itself to avoid recursion
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
    
    # Build code index for search
    workspace_root = workspace or os.getenv("GITHUB_WORKSPACE", os.getcwd())
    index = CodeIndex(workspace_root)
    index.build()
    
    # Define tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_codebase",
                "description": "Search the indexed codebase for a query string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text to search in the repository.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 6,
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    ]
    
    tool_map = {
        "search_codebase": lambda query, max_results=6: {
            "query": query,
            "results": index.search(query, max_results=max_results),
        }
    }
    
    # Check if we should force approval due to max iterations
    force_approve = iteration >= MAX_ITERATIONS
    if force_approve:
        logger.info("Max iterations reached, will force approval")
    
    # Build prompts
    system_prompt = (
        "You are an expert code reviewer. Analyze the pull request and provide "
        "a structured review decision. Be thorough but concise. "
        "Use the search_codebase tool when you need additional context from the repository."
    )
    
    user_prompt = _build_review_prompt(
        pr_title=pr.title,
        pr_body=pr.body,
        diff_summary=diff_summary,
        ci_status=ci_status,
        issue=issue,
        iteration=iteration,
        max_iterations=MAX_ITERATIONS,
    )
    
    # Run the review with tool support
    report = _create_review_with_tools(
        client=openai_client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        tool_map=tool_map,
    )
    
    # Parse the review response
    parsed = _parse_review_response(report)
    
    # Force approval if max iterations reached
    if force_approve and parsed.get("status") == "CHANGES_REQUESTED":
        parsed["status"] = "APPROVED"
        parsed["summary"] = (
            f"**Forced approval after {MAX_ITERATIONS} iterations.** "
            f"Original assessment: {parsed.get('summary', 'N/A')}"
        )
        parsed["issues"].insert(0, "This PR exceeded the maximum iteration limit and was auto-approved.")
    
    # Check if CI is failing - block approval
    ci_failing = any(
        c.status == "completed" and c.conclusion not in ("success", "skipped", "neutral")
        for c in check_runs
    )
    if ci_failing and parsed.get("status") == "APPROVED" and not force_approve:
        parsed["status"] = "CHANGES_REQUESTED"
        parsed["issues"] = parsed.get("issues", []) + ["CI checks are failing. Please fix before approval."]
    
    decision = ReviewDecision(
        status=parsed.get("status", "CHANGES_REQUESTED"),
        summary=parsed.get("summary", "Review completed."),
        issues=parsed.get("issues", []),
        iteration=iteration,
        max_iterations=MAX_ITERATIONS,
    )
    
    # Post the review comment (for machine-readable feedback)
    comment = _format_review_comment(decision, pr.url, pr.head_ref)
    client.comment_pull_request(pr.number, comment)
    
    # Post a proper PR review (APPROVE or REQUEST_CHANGES)
    review_body = _format_review_body(decision)
    review_event = "APPROVE" if decision.status == "APPROVED" else "REQUEST_CHANGES"
    try:
        client.create_pull_request_review(pr.number, body=review_body, event=review_event)
        logger.info("Posted PR review with event: %s", review_event)
    except Exception as e:
        logger.warning("Failed to post PR review: %s", e)
    
    # Write to GitHub Actions summary if running in Actions
    _write_actions_summary(decision, pr.url, pr.head_ref)
    
    logger.info("Review completed: %s", decision.status)
    return decision


def _create_review_with_tools(
    *,
    client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
    tool_map: dict,
) -> str:
    """Run the review with tool calling support."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    
    max_iterations = 10
    response = None
    
    for _ in range(max_iterations):
        response = chat_with_tools(
            client=client,
            model=model,
            messages=messages,
            tools=tools,
        )
        
        # Add assistant message to history
        assistant_msg: dict = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ]
        messages.append(assistant_msg)
        
        if not response.tool_calls:
            return (response.content or "").strip()
        
        # Execute tool calls
        for call in response.tool_calls:
            result = _run_tool(call.name, call.arguments, tool_map)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })
    
    return (response.content if response else "Review generation incomplete.").strip()


def _run_tool(name: str, raw_arguments: str | None, tool_map: dict) -> dict:
    """Execute a tool call."""
    tool = tool_map.get(name)
    if not tool:
        return {"error": f"Unknown tool: {name}"}
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        arguments = {}
    try:
        return tool(**arguments)
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    
    token = _require_env("GH_TOKEN")
    repo = _require_env("GH_REPOSITORY")
    pr_number_raw = _require_env("PR_NUMBER")
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    llm_token = os.getenv("LLM_API_TOKEN")
    llm_url = os.getenv("LLM_API_URL")
    llm_verify_ssl = os.getenv("LLM_VERIFY_SSL", "true").strip().lower() != "false"
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        pr_number = int(pr_number_raw)
    except ValueError:
        print("PR_NUMBER must be an integer.")
        return 0

    client = GitHubClient(token=token, repo_full_name=repo)
    _require_env("LLM_API_TOKEN")
    _require_env("LLM_API_URL")
    openai_client = build_client(
        provider=provider,
        api_token=llm_token,
        api_url=llm_url,
        verify_ssl=llm_verify_ssl,
    )
    run_reviewer(
        client=client,
        pr_number=pr_number,
        openai_client=openai_client,
        model=openai_model,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

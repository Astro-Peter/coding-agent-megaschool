from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agents.common.code_index import CodeIndex
from agents.common.github_client import GitHubClient, IssueCommentData, IssueData
from agents.common.openai_client import build_client, chat_with_tools
from agents.coder_agent.tools import (
    FileWorkspace,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    create_mark_complete_tool,
)
from agents.planner_agent.cli import PLAN_MARKER


logger = logging.getLogger(__name__)

# Max iterations for the agent loop (LLM calls)
MAX_AGENT_ITERATIONS = 50

# Max development iterations (plan -> code -> review cycle)
MAX_DEV_ITERATIONS = 5

# Label prefix for tracking iterations
ITERATION_LABEL_PREFIX = "iteration-"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}")
        sys.exit(0)
    return value


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


def _get_iteration_count(client: GitHubClient, issue_number: int) -> int:
    """Get current iteration count from issue labels."""
    labels = client.get_issue_labels(issue_number)
    for label in labels:
        if label.startswith(ITERATION_LABEL_PREFIX):
            try:
                return int(label.split("-")[1])
            except (ValueError, IndexError):
                pass
    return 0  # No iteration label yet


def _update_iteration_count(client: GitHubClient, issue_number: int, new_count: int) -> None:
    """Update the iteration count label on an issue."""
    labels = client.get_issue_labels(issue_number)
    
    # Remove old iteration labels
    for label in labels:
        if label.startswith(ITERATION_LABEL_PREFIX):
            client.remove_issue_label(issue_number, label)
    
    # Add new iteration label
    new_label = f"{ITERATION_LABEL_PREFIX}{new_count}"
    client.add_issue_label(issue_number, new_label)
    logger.info("Updated iteration label to %s for issue #%d", new_label, issue_number)


def _find_existing_branch(client: GitHubClient, issue_number: int) -> str | None:
    """Find existing coder-agent branch for this issue."""
    # Check open PRs for branches matching our pattern
    prs = client.list_pull_requests(state="open")
    for pr in prs:
        if pr.head_ref.startswith(f"coder-agent/issue-{issue_number}-"):
            return pr.head_ref
    return None


def _clone_repository(clone_url: str, token: str, dest: Path) -> bool:
    """Clone the repository to dest directory. Returns True on success."""
    # Inject token into URL for authentication
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
        # Check if there are changes to commit
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
            env={**os.environ, "GIT_AUTHOR_NAME": "Coder Agent", "GIT_AUTHOR_EMAIL": "agent@example.com",
                 "GIT_COMMITTER_NAME": "Coder Agent", "GIT_COMMITTER_EMAIL": "agent@example.com"},
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


def _build_system_prompt(
    issue: IssueData,
    plan: dict,
    iteration: int,
    reviewer_feedback: list[str] | None = None,
) -> str:
    """Build the system prompt for the coder agent."""
    steps = plan.get("steps", [])
    steps_text = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(steps))

    feedback_section = ""
    if reviewer_feedback and len(reviewer_feedback) > 0:
        feedback_items = "\n".join(f"  - {item}" for item in reviewer_feedback)
        feedback_section = f"""
## Reviewer Feedback (PRIORITY - Address these issues first!)
This is iteration {iteration}/{MAX_DEV_ITERATIONS}. The reviewer found the following issues:
{feedback_items}

You MUST address these issues before proceeding with any other changes.
"""

    iteration_note = ""
    if iteration > 1:
        iteration_note = f"""
## Iteration Note
This is iteration {iteration}/{MAX_DEV_ITERATIONS} of the development cycle.
Previous attempts had issues that need to be fixed. Focus on the reviewer feedback.
"""

    return f"""You are an expert coding agent. Your task is to implement code changes based on a plan.

## Issue
Title: {issue.title}
Body: {issue.body}

## Plan
Summary: {plan.get('summary', 'No summary')}
Steps:
{steps_text}
{iteration_note}{feedback_section}
## Instructions
1. First, explore the codebase using list_dir and read_file to understand the structure.
2. Use search_codebase to find relevant code related to the issue.
3. Implement the changes step by step using write_file, create_file, replace_in_file, etc.
4. When you have completed ALL steps, call mark_complete with a summary of what you did.

## Rules
- Always read a file before modifying it.
- Make minimal, focused changes.
- Follow existing code style and conventions.
- Do not create unnecessary files.
- If you cannot complete a step, explain why in your mark_complete summary.
- If there is reviewer feedback, address it FIRST before other changes.
"""


def _build_tools(workspace: FileWorkspace, index: CodeIndex, completion_callback) -> ToolRegistry:
    """Build the tool registry with all available tools."""
    registry = ToolRegistry()

    # Add file workspace tools
    for tool in workspace.tools():
        registry.register(tool)

    # Add code search tool
    def search_handler(args: dict) -> dict:
        query = args.get("query", "")
        max_results = args.get("max_results", 6)
        results = index.search(query, max_results=max_results)
        return ToolResult(ok=True, message="ok", data={"results": results}).as_dict()

    registry.register(ToolSpec(
        name="search_codebase",
        description="Search the codebase for a query string. Returns matching files with snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for in the codebase."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6,
                               "description": "Maximum number of results to return."},
            },
            "required": ["query"],
        },
        handler=search_handler,
    ))

    # Add mark_complete tool
    registry.register(create_mark_complete_tool(completion_callback))

    return registry


def _run_agent_loop(
    *,
    openai_client,
    model: str,
    system_prompt: str,
    tools: ToolRegistry,
    max_iterations: int = MAX_AGENT_ITERATIONS,
) -> str | None:
    """Run the agentic loop until mark_complete is called or max iterations reached."""
    completion_summary: list[str] = []

    def on_complete(summary: str) -> None:
        completion_summary.append(summary)

    # Update the mark_complete tool callback
    mark_complete_tool = create_mark_complete_tool(on_complete)
    tools.register(mark_complete_tool)

    tool_specs = tools.specs()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Please implement the changes according to the plan. Start by exploring the codebase structure."},
    ]

    for iteration in range(max_iterations):
        logger.info("Agent iteration %d", iteration + 1)

        # Check for completion
        if completion_summary:
            return completion_summary[0]

        # Make the API call
        response = chat_with_tools(
            client=openai_client,
            model=model,
            messages=messages,
            tools=tool_specs,
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
            # No tool calls, check if there's text output
            if response.content:
                logger.info("Agent response: %s", response.content[:200])
            # Ask agent to continue
            messages.append({
                "role": "user",
                "content": "Please continue implementing or call mark_complete if done.",
            })
            continue

        # Execute tool calls and add results to messages
        for call in response.tool_calls:
            logger.info("Calling tool: %s", call.name)
            try:
                arguments = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}

            result = tools.call(call.name, arguments)
            logger.debug("Tool result: %s", str(result)[:200])

            # Add tool result to messages
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })

            # Check if mark_complete was called
            if completion_summary:
                return completion_summary[0]

    logger.warning("Max iterations reached without completion")
    return "Max iterations reached. Partial implementation may be available."


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


def run_coder(
    *,
    client: GitHubClient,
    issue_number: int,
    openai_client,
    model: str,
    reviewer_feedback: list[str] | None = None,
) -> None:
    """Main entry point for the coder agent."""
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
                "Please run the planner agent first with `/plan`.",
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

    # Check if there's an existing branch to update
    existing_branch = _find_existing_branch(client, issue_number)
    is_update = existing_branch is not None
    
    # Create temporary directory for clone
    temp_dir = tempfile.mkdtemp(prefix="coder_agent_")
    clone_path = Path(temp_dir) / "repo"

    try:
        # Clone repository
        clone_url = client.get_clone_url()
        token = os.getenv("GITHUB_TOKEN", "")

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
                # Fall back to creating a new branch
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
            # Create new branch
            random_suffix = secrets.token_hex(4)
            branch_name = f"coder-agent/issue-{issue.number}-{random_suffix}"
            if not _git_create_branch(clone_path, branch_name):
                client.comment_issue(
                    issue.number,
                    f"🧩 **Coder Agent failed to create branch `{branch_name}`.**",
                )
                return

        # Build tools
        workspace = FileWorkspace(clone_path)
        index = CodeIndex(str(clone_path))
        index.build()

        completion_summary: list[str] = []
        tools = _build_tools(workspace, index, lambda s: completion_summary.append(s))

        # Build prompt and run agent
        system_prompt = _build_system_prompt(
            issue,
            plan,
            iteration=new_iteration,
            reviewer_feedback=reviewer_feedback,
        )
        summary = _run_agent_loop(
            openai_client=openai_client,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

        if not summary:
            summary = "Agent loop completed without explicit completion."

        # Commit and push changes
        commit_prefix = "fix" if is_update else "feat"
        commit_message = f"{commit_prefix}: implement changes for #{issue.number} (iteration {new_iteration})\n\n{summary}"
        has_changes = _git_commit(clone_path, commit_message)

        if has_changes:
            if _git_push(clone_path, branch_name):
                if is_update:
                    # Just comment on the issue, PR already exists
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
                    # Create new PR
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
        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

    token = _require_env("GITHUB_TOKEN")
    repo = _require_env("GITHUB_REPOSITORY")
    issue_number_raw = _require_env("ISSUE_NUMBER")
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    llm_token = os.getenv("LLM_API_TOKEN")
    llm_url = os.getenv("LLM_API_URL")
    llm_verify_ssl = os.getenv("LLM_VERIFY_SSL", "true").strip().lower() != "false"
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        issue_number = int(issue_number_raw)
    except ValueError:
        print("ISSUE_NUMBER must be an integer.")
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
    run_coder(
        client=client,
        issue_number=issue_number,
        openai_client=openai_client,
        model=openai_model,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

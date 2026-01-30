"""Unified watcher that handles all agent events.

Events handled:
- New issues: Auto-run planner
- New plans: Auto-trigger coder (configurable via AUTO_CODE_AFTER_PLAN)
- PR updates: Run reviewer
- Reviewer feedback: Auto-trigger coder for fixes

This creates a full SDLC automation loop:
1. User creates issue -> Planner creates plan
2. Plan created -> Coder implements changes and creates PR
3. PR created/updated -> Reviewer analyzes changes
4. Reviewer requests changes -> Coder fixes issues
5. Repeat until approved or max iterations reached
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from github_agents.common.config import load_config
from github_agents.common.github_client import GitHubClient
from github_agents.orchestrator import Orchestrator
from github_agents.planner_agent.agent import PLAN_MARKER
from github_agents.reviewer_agent.agent import REVIEWER_FEEDBACK_MARKER

STATE_PATH = Path(".watcher_state.json")
logger = logging.getLogger(__name__)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _should_skip_author(author: str) -> bool:
    return author in {"github-actions[bot]", "dependabot[bot]"}


def _check_new_issues(
    client: GitHubClient,
    orchestrator: Orchestrator,
    state: dict,
) -> None:
    """Check for new issues and auto-plan them."""
    last_seen = _parse_timestamp(state.get("last_issue_created_at"))

    issues = client.list_issues(state="open")
    for issue in reversed(issues):
        if issue.is_pull_request:
            continue
        if issue.user_login and _should_skip_author(issue.user_login):
            continue
        if issue.created_at and (last_seen is None or issue.created_at > last_seen):
            print(f"New issue #{issue.number}: {issue.title}")
            orchestrator.plan(issue_number=issue.number, command="issue opened")
            last_seen = issue.created_at

    if last_seen:
        state["last_issue_created_at"] = last_seen.isoformat()


def _check_pr_updates(
    client: GitHubClient,
    orchestrator: Orchestrator,
    state: dict,
) -> None:
    """Check for PR updates and run reviewer."""
    last_seen_pr_updates: dict[str, str] = state.get("last_seen_pr_updates", {})

    prs = client.list_pull_requests(state="open")
    for pr in prs:
        pr_key = str(pr.number)
        last_seen = _parse_timestamp(last_seen_pr_updates.get(pr_key))

        if last_seen is None or pr.updated_at > last_seen:
            print(f"PR #{pr.number} updated: {pr.title}")
            orchestrator.review(pr_number=pr.number)
            last_seen_pr_updates[pr_key] = pr.updated_at.isoformat()

    state["last_seen_pr_updates"] = last_seen_pr_updates


def _extract_issue_from_pr_body(body: str) -> int | None:
    """Extract linked issue number from PR body."""
    patterns = [
        r"(?:closes|fixes|resolves|addresses|for)\s*#(\d+)",
        r"issue[:\s]*#?(\d+)",
        r"#(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _parse_reviewer_feedback(comment_body: str) -> dict | None:
    """Parse machine-readable feedback from reviewer comment."""
    if REVIEWER_FEEDBACK_MARKER not in comment_body:
        return None

    # Look for the JSON block in the details section
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", comment_body, re.DOTALL)
    if not json_match:
        return None

    try:
        return json.loads(json_match.group(1))
    except json.JSONDecodeError:
        return None


def _check_reviewer_feedback(
    client: GitHubClient,
    orchestrator: Orchestrator,
    state: dict,
) -> None:
    """Check for reviewer feedback that requests changes and trigger coder."""
    last_seen_feedback: dict[str, int] = state.get("last_seen_feedback", {})

    prs = client.list_pull_requests(state="open")
    for pr in prs:
        pr_key = str(pr.number)
        last_seen_id = last_seen_feedback.get(pr_key, 0)

        # Get comments on this PR
        try:
            comments = client.list_pr_comments(pr.number)
        except Exception as e:
            logger.warning("Failed to get PR comments for #%d: %s", pr.number, e)
            continue

        comments_sorted = sorted(comments, key=lambda c: c.created_at)

        for comment in comments_sorted:
            if comment.id <= last_seen_id:
                continue
            last_seen_id = comment.id

            # Check if this is a reviewer feedback comment
            feedback = _parse_reviewer_feedback(comment.body)
            if not feedback:
                continue

            status = feedback.get("status")
            iteration = feedback.get("iteration", 1)
            max_iterations = feedback.get("max_iterations", 5)
            issues = feedback.get("issues", [])

            logger.info(
                "Found reviewer feedback on PR #%d: status=%s, iteration=%d/%d",
                pr.number,
                status,
                iteration,
                max_iterations,
            )

            if status == "CHANGES_REQUESTED" and iteration < max_iterations:
                # Extract linked issue from PR body
                issue_number = _extract_issue_from_pr_body(pr.body)
                if issue_number:
                    print(
                        f"Reviewer requested changes on PR #{pr.number}, triggering coder for issue #{issue_number}"
                    )
                    # Pass the feedback to the coder
                    orchestrator.code(
                        issue_number=issue_number,
                        reviewer_feedback=issues,
                    )
                else:
                    logger.warning(
                        "Could not find linked issue for PR #%d, cannot trigger coder", pr.number
                    )

        last_seen_feedback[pr_key] = last_seen_id

    state["last_seen_feedback"] = last_seen_feedback


def _check_new_plans(
    client: GitHubClient,
    orchestrator: Orchestrator,
    state: dict,
    *,
    enabled: bool = True,
) -> None:
    """Check for new planner comments and auto-trigger coder."""
    if not enabled:
        return

    last_seen_plans: dict[str, int] = state.get("last_seen_plans", {})

    issues = client.list_issues(state="open")
    for issue in issues:
        if issue.is_pull_request:
            continue

        issue_key = str(issue.number)
        last_seen_id = last_seen_plans.get(issue_key, 0)

        comments = client.list_issue_comments(issue.number)
        comments_sorted = sorted(comments, key=lambda c: c.created_at)

        for comment in comments_sorted:
            if comment.id <= last_seen_id:
                continue
            last_seen_id = comment.id

            # Check if this is a planner comment (from bot)
            body = comment.body or ""
            if PLAN_MARKER not in body:
                continue

            # This is a planner comment - auto-trigger coder
            logger.info("Found new plan for issue #%d, auto-triggering coder", issue.number)
            print(f"New plan detected for issue #{issue.number}, auto-triggering coder...")
            orchestrator.code(issue_number=issue.number)

        last_seen_plans[issue_key] = last_seen_id

    state["last_seen_plans"] = last_seen_plans


def main() -> int:
    load_dotenv()
    cfg = load_config()

    poll_seconds = float(os.getenv("POLL_SECONDS", "15"))
    auto_code = os.getenv("AUTO_CODE_AFTER_PLAN", "true").strip().lower() == "true"

    orchestrator = Orchestrator(
        client=cfg.gh_client,
        openai_client=cfg.llm_client,
        model=cfg.model,
    )

    state = _load_state()
    client = cfg.gh_client

    repo = os.getenv("GH_REPOSITORY", "")
    print(f"Watching repository {repo} for events (polling every {poll_seconds}s)...")
    events = "new issues, PR updates, reviewer feedback"
    if auto_code:
        events += ", auto-code after plan"
    print(f"Events: {events}")

    while True:
        try:
            _check_new_issues(client, orchestrator, state)
            _check_new_plans(client, orchestrator, state, enabled=auto_code)
            _check_pr_updates(client, orchestrator, state)
            _check_reviewer_feedback(client, orchestrator, state)
            _save_state(state)
        except Exception as e:
            logger.exception("Error during poll: %s", e)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
from agents.common.github_client import GitHubClient
from agents.common.openai_client import build_client
from agents.planner_agent.planner import build_plan


PLAN_MARKER = "<!-- planner-agent-plan -->"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}")
        sys.exit(0)
    return value


def run_planner(
    *,
    client: GitHubClient,
    issue_number: int,
    openai_client,
    model: str,
    plan_command: str | None = None,
) -> None:
    issue = client.get_issue(issue_number)
    plan = build_plan(
        issue.title,
        issue.body,
        llm_client=openai_client,
        model=model,
    )

    plan_json = json.dumps({"summary": plan.summary, "steps": plan.steps}, indent=2)
    body_lines = [
        PLAN_MARKER,
        "🧭 **Planner Agent created a plan.**",
        "",
        f"- Issue: {issue.url}",
        f"- Summary: {plan.summary}",
    ]
    if plan_command:
        body_lines.append(f"- Requested by: `{plan_command}`")
    body_lines.extend(
        [
            "",
            "Planned steps:",
            *[f"  {idx + 1}. {step}" for idx, step in enumerate(plan.steps)],
            "",
            "Plan data (for other agents):",
            "```json",
            plan_json,
            "```",
            "",
            "Next: trigger the coder agent with `/code step=1`.",
        ]
    )
    client.comment_issue(issue.number, "\n".join(body_lines))


def main() -> int:
    token = _require_env("GH_TOKEN")
    repo = _require_env("GH_REPOSITORY")
    issue_number_raw = _require_env("ISSUE_NUMBER")
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    llm_token = os.getenv("LLM_API_TOKEN")
    llm_url = os.getenv("LLM_API_URL")
    llm_verify_ssl = os.getenv("LLM_VERIFY_SSL", "true").strip().lower() != "false"

    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    plan_command = os.getenv("PLAN_COMMAND")

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
    run_planner(
        client=client,
        issue_number=issue_number,
        openai_client=openai_client,
        model=openai_model,
        plan_command=plan_command,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

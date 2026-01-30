"""Planner Agent using OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from agents import Agent, Runner
from pydantic import BaseModel

from github_agents.common.config import get_issue_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.sdk_config import configure_sdk, get_model_name
from github_agents.planner_agent.prompts import (
    PLANNER_SYSTEM_INSTRUCTIONS,
    build_planner_prompt,
)

logger = logging.getLogger(__name__)

PLAN_MARKER = "<!-- planner-agent-plan -->"


class Plan(BaseModel):
    """Structured plan output from the planner agent."""

    summary: str
    steps: list[str]


def _create_planner_agent() -> Agent[AgentContext]:
    """Create the planner agent with the configured model."""
    return Agent[AgentContext](
        name="Planner",
        model=get_model_name(),
        instructions=PLANNER_SYSTEM_INSTRUCTIONS,
        output_type=Plan,
    )


async def build_plan_async(
    issue_title: str,
    issue_body: str,
    context: AgentContext,
) -> Plan:
    """Build a plan for the given issue using the planner agent.

    Args:
        issue_title: The title of the GitHub issue.
        issue_body: The body/description of the GitHub issue.
        context: The agent context with dependencies.

    Returns:
        A Plan with summary and steps.
    """
    prompt = build_planner_prompt(issue_title, issue_body)

    try:
        # Create agent with LiteLLM model
        agent = _create_planner_agent()
        result = await Runner.run(
            agent,
            prompt,
            context=context,
        )
        return result.final_output_as(Plan)
    except Exception as exc:
        logger.exception("Failed to generate plan: %s", exc)
        # Return a fallback plan
        return Plan(
            summary=f"Draft plan for: {issue_title}".strip() or "Implementation plan",
            steps=[
                "Analyze requirements from the issue description and repo context.",
                "Implement the first step and validate locally.",
                "Iterate remaining steps with separate sessions if needed.",
                "Run checks/tests and open a pull request.",
            ],
        )


def build_plan(
    issue_title: str,
    issue_body: str,
    *,
    context: AgentContext,
) -> Plan:
    """Synchronous wrapper for build_plan_async."""
    return asyncio.run(build_plan_async(issue_title, issue_body, context))


async def run_planner_async(
    *,
    context: AgentContext,
    plan_command: str | None = None,
) -> Plan:
    """Run the planner agent and post results to GitHub.

    Args:
        context: The agent context with GitHub client and issue number.
        plan_command: Optional command that triggered the planning.

    Returns:
        The generated Plan.
    """
    if context.issue_number is None:
        raise ValueError("issue_number is required in context")

    gh_client = context.gh_client
    issue = gh_client.get_issue(context.issue_number)

    plan = await build_plan_async(issue.title, issue.body, context)

    # Format and post the plan as a comment
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
            "The Coder Agent will automatically implement this plan.",
        ]
    )
    gh_client.comment_issue(issue.number, "\n".join(body_lines))

    return plan


def run_planner(
    *,
    context: AgentContext,
    plan_command: str | None = None,
) -> Plan:
    """Synchronous wrapper for run_planner_async."""
    return asyncio.run(run_planner_async(context=context, plan_command=plan_command))


def main() -> int:
    """CLI entry point for the planner agent."""
    cfg = load_config()
    issue_number = get_issue_number()
    plan_command = os.getenv("PLAN_COMMAND")

    # Configure the SDK for OpenRouter
    configure_sdk()

    # Create context
    context = AgentContext(
        gh_client=cfg.gh_client,
        model=cfg.model,
        issue_number=issue_number,
    )

    run_planner(context=context, plan_command=plan_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

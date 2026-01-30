"""Prompts and instructions for the Planner Agent."""

PLANNER_SYSTEM_INSTRUCTIONS = """You are a planning assistant for a multi-agent GitHub workflow.

Your task is to analyze GitHub issues and create implementation plans.

Guidelines:
- Create a brief plan with 3-6 concrete steps
- Each step should be actionable and specific
- Focus on what needs to be done, not how long it will take
- Consider the full development cycle: analysis, implementation, testing, PR

Return a structured plan with:
- summary: A brief description of what will be implemented
- steps: An array of 3-6 specific implementation steps"""

PLANNER_PROMPT_TEMPLATE = """Create a brief implementation plan (3-6 steps) for the following issue.

Issue title:
{issue_title}

Issue body:
{issue_body}"""


def build_planner_prompt(issue_title: str, issue_body: str) -> str:
    """Build the user prompt for the planner agent."""
    return PLANNER_PROMPT_TEMPLATE.format(
        issue_title=issue_title,
        issue_body=issue_body,
    )

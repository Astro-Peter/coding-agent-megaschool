from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from agents.common.openai_client import create_text

logger = logging.getLogger(__name__)


@dataclass
class Plan:
    summary: str
    steps: list[str]


def build_plan(
    issue_title: str,
    issue_body: str,
    *,
    llm_client,
    model: str,
) -> Plan:
    summary = "Initial analysis complete; ready to implement the requested change."
    steps = [
        "Analyze requirements from the issue description and repo context.",
        "Implement the first step and validate locally.",
        "Iterate remaining steps with separate sessions if needed.",
        "Run checks/tests and open a pull request.",
    ]
    if issue_title or issue_body:
        summary = f"Draft plan for: {issue_title}".strip()

    system_prompt = (
        "You are a planning assistant for a multi-agent GitHub workflow. "
        "Use the issue details and repository context to outline a plan. "
        "Return only JSON with keys: summary (string), steps (array of strings)."
    )
    user_prompt = (
        "Create a brief plan (3-6 steps) for implementing the issue. "
        "Issue title:\n"
        f"{issue_title}\n\nIssue body:\n{issue_body}"
    )
    try:
        raw = create_text(
            client=llm_client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.info("LLM raw response: %s", raw[:500] if raw else "<empty>")
        # Strip markdown code fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
        parsed = json.loads(clean)
        summary = str(parsed.get("summary", summary)).strip() or summary
        steps_raw = parsed.get("steps", steps)
        if isinstance(steps_raw, list) and steps_raw:
            steps = [str(step).strip() for step in steps_raw if str(step).strip()]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to parse LLM response as JSON: %s", exc)
    except Exception as exc:
        logger.exception("LLM call failed: %s", exc)
    return Plan(summary=summary, steps=steps)

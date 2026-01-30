"""CI Fixer Agent using OpenAI Agents SDK.

This agent triggers on CI check failures and analyzes what went wrong,
posting detailed comments with suggestions for how to fix the issues.
The coder agent then picks up these suggestions in a subsequent job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from github_agents.ci_fixer_agent.prompts import (
    CI_ANALYZER_SYSTEM_INSTRUCTIONS,
    build_ci_analysis_prompt,
)
from github_agents.common.config import get_pr_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.github_client import CheckRunData, WorkflowLogData
from github_agents.common.sdk_config import configure_sdk, get_model_name

logger = logging.getLogger(__name__)


# Marker for machine-readable CI analysis
CI_FIXER_MARKER = "<!-- ci-fixer-agent-report -->"


class CIFixSuggestion(BaseModel):
    """A single suggestion for fixing a CI issue."""

    file: str = Field(description="The file path where the issue occurs")
    line: int | None = Field(default=None, description="Line number if applicable")
    issue: str = Field(description="Description of the issue")
    suggestion: str = Field(description="Suggested fix or action to take")


class CIAnalysis(BaseModel):
    """Structured output from the CI fixer agent."""

    status: Literal["ANALYZED", "NO_ISSUES", "UNABLE_TO_ANALYZE"] = Field(
        description="Analysis status"
    )
    summary: str = Field(description="Brief overall summary of the CI failures")
    failed_checks: list[str] = Field(
        default_factory=list, description="Names of the checks that failed"
    )
    root_causes: list[str] = Field(
        default_factory=list, description="Identified root causes of the failures"
    )
    suggestions: list[CIFixSuggestion] = Field(
        default_factory=list, description="Specific suggestions for fixing each issue"
    )


# --- Helper Functions ---


def _format_check_failure(check: CheckRunData) -> str:
    """Format a single check failure with all available details."""
    lines = [
        f"### ❌ {check.name}",
        f"- **Status:** {check.conclusion}",
        f"- **URL:** {check.html_url}",
    ]

    if check.output_title:
        lines.append(f"- **Title:** {check.output_title}")

    if check.output_summary:
        # Truncate long summaries
        summary = check.output_summary
        if len(summary) > 2000:
            summary = summary[:2000] + "\n... (truncated)"
        lines.append(f"\n**Summary:**\n{summary}")

    if check.annotations:
        lines.append("\n**Annotations:**")
        for ann in check.annotations[:20]:  # Limit to first 20 annotations
            level_emoji = {"failure": "❌", "warning": "⚠️", "notice": "ℹ️"}.get(
                ann.annotation_level, "•"
            )
            lines.append(f"- {level_emoji} `{ann.path}:{ann.start_line}` - {ann.message}")
            if ann.title:
                lines.append(f"  Title: {ann.title}")

        if len(check.annotations) > 20:
            lines.append(f"  ... and {len(check.annotations) - 20} more annotations")

    return "\n".join(lines)


def _format_all_failures(failed_checks: list[CheckRunData]) -> str:
    """Format all check failures for the agent prompt."""
    if not failed_checks:
        return "No failed checks found."

    lines = [f"## Failed CI Checks ({len(failed_checks)} total)\n"]
    for check in failed_checks:
        lines.append(_format_check_failure(check))
        lines.append("")

    return "\n".join(lines)


def _format_workflow_logs(logs_by_workflow: dict[str, list[WorkflowLogData]]) -> str:
    """Format workflow logs for the agent prompt."""
    if not logs_by_workflow:
        return "No workflow logs available."

    lines = ["## Workflow Logs\n"]

    for workflow_name, logs in logs_by_workflow.items():
        lines.append(f"### Workflow: {workflow_name}\n")

        for log in logs:
            lines.append(f"**Job: {log.job_name}**")

            if log.error_lines:
                lines.append("\n**Extracted Errors:**")
                for err in log.error_lines[:30]:  # Limit errors shown
                    lines.append(f"- {err}")

            # Include truncated log content
            content = log.log_content
            if len(content) > 3000:
                content = "... (earlier output truncated) ...\n" + content[-3000:]
            lines.append(f"\n**Log Output:**\n```\n{content}\n```\n")

        lines.append("")

    return "\n".join(lines)


def _format_annotations(failed_checks: list[CheckRunData]) -> str:
    """Format all annotations from failed checks."""
    all_annotations = []
    for check in failed_checks:
        if check.annotations:
            for ann in check.annotations:
                all_annotations.append(
                    {
                        "check": check.name,
                        "file": ann.path,
                        "line": ann.start_line,
                        "level": ann.annotation_level,
                        "message": ann.message,
                        "title": ann.title,
                    }
                )

    if not all_annotations:
        return "No structured annotations available."

    lines = ["## Structured Error Annotations\n"]
    for ann in all_annotations[:50]:  # Limit to 50 annotations
        lines.append(f"- **{ann['file']}:{ann['line']}** ({ann['check']})")
        lines.append(f"  - Level: {ann['level']}")
        lines.append(f"  - Message: {ann['message']}")
        if ann["title"]:
            lines.append(f"  - Title: {ann['title']}")

    if len(all_annotations) > 50:
        lines.append(f"\n... and {len(all_annotations) - 50} more annotations")

    return "\n".join(lines)


def _format_diff_context(files: list, max_size: int = 3000) -> str:
    """Format relevant parts of the PR diff for context."""
    if not files:
        return "No changed files."

    lines = [f"Changed files ({len(files)} total):"]
    for f in files:
        lines.append(f"  - {f.filename} (+{f.additions}/-{f.deletions})")

    return "\n".join(lines)


def _format_analysis_comment(analysis: CIAnalysis, pr_url: str) -> str:
    """Format the CI analysis as a GitHub comment."""
    status_emoji = {
        "ANALYZED": "🔍",
        "NO_ISSUES": "✅",
        "UNABLE_TO_ANALYZE": "⚠️",
    }.get(analysis.status, "❓")

    lines = [
        CI_FIXER_MARKER,
        f"## {status_emoji} CI Failure Analysis",
        "",
        f"**PR:** {pr_url}",
        "",
        "### Summary",
        analysis.summary,
        "",
    ]

    if analysis.failed_checks:
        lines.extend(
            [
                "### Failed Checks",
                *[f"- ❌ {check}" for check in analysis.failed_checks],
                "",
            ]
        )

    if analysis.root_causes:
        lines.extend(
            [
                "### Root Causes",
                *[f"- {cause}" for cause in analysis.root_causes],
                "",
            ]
        )

    if analysis.suggestions:
        lines.extend(
            [
                "### Suggested Fixes",
                "",
            ]
        )
        for i, sugg in enumerate(analysis.suggestions, 1):
            lines.append(f"**{i}. {sugg.file}**")
            if sugg.line:
                lines.append(f"   - Line: {sugg.line}")
            lines.append(f"   - Issue: {sugg.issue}")
            lines.append(f"   - Fix: {sugg.suggestion}")
            lines.append("")

    lines.extend(
        [
            "---",
            "*This analysis was generated automatically by the CI Fixer Agent.*",
            "*The Coder Agent will now attempt to fix these issues.*",
        ]
    )

    # Add machine-readable data
    data_block = {
        "status": analysis.status,
        "failed_checks": analysis.failed_checks,
        "root_causes": analysis.root_causes,
        "suggestion_count": len(analysis.suggestions),
    }
    lines.extend(
        [
            "",
            "<details>",
            "<summary>Machine-readable data</summary>",
            "",
            "```json",
            json.dumps(data_block, indent=2),
            "```",
            "",
            "</details>",
        ]
    )

    return "\n".join(lines)


def _write_actions_summary(analysis: CIAnalysis, pr_url: str) -> None:
    """Write analysis summary to GitHub Actions summary file."""
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    status_emoji = {
        "ANALYZED": "🔍",
        "NO_ISSUES": "✅",
        "UNABLE_TO_ANALYZE": "⚠️",
    }.get(analysis.status, "❓")

    lines = [
        f"# {status_emoji} CI Failure Analysis",
        "",
        f"**PR:** {pr_url}",
        "",
        "## Summary",
        analysis.summary,
        "",
    ]

    if analysis.failed_checks:
        lines.extend(
            [
                "## Failed Checks",
                "| Check Name | Status |",
                "|------------|--------|",
                *[f"| {check} | ❌ Failed |" for check in analysis.failed_checks],
                "",
            ]
        )

    if analysis.suggestions:
        lines.extend(
            [
                "## Suggested Fixes",
                "",
            ]
        )
        for sugg in analysis.suggestions:
            loc = f"{sugg.file}:{sugg.line}" if sugg.line else sugg.file
            lines.append(f"- **{loc}**: {sugg.suggestion}")
        lines.append("")

    try:
        with open(summary_file, "a") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("Wrote CI analysis to GitHub Actions summary")
    except Exception as e:
        logger.warning("Failed to write Actions summary: %s", e)


# --- Agent Definition ---


def _build_ci_fixer_agent() -> Agent[AgentContext]:
    """Build the CI fixer agent (no tools, just structured output)."""
    return Agent[AgentContext](
        name="CIAnalyzer",
        model=get_model_name(),
        instructions=CI_ANALYZER_SYSTEM_INSTRUCTIONS,
        tools=[],  # No tools - just analyze the provided information
        output_type=CIAnalysis,
    )


# --- Agent Execution ---


async def run_ci_analysis_async(
    prompt: str,
    context: AgentContext,
) -> CIAnalysis:
    """Run the CI analyzer agent and return the analysis."""
    agent = _build_ci_fixer_agent()

    try:
        result = await Runner.run(
            agent,
            prompt,
            context=context,
            max_turns=1,  # Single turn - no tools to call
        )
        return result.final_output_as(CIAnalysis)
    except Exception as exc:
        logger.exception("CI analysis failed: %s", exc)
        return CIAnalysis(
            status="UNABLE_TO_ANALYZE",
            summary=f"Failed to analyze CI failures: {exc}",
            failed_checks=[],
            root_causes=[],
            suggestions=[],
        )


async def run_ci_fixer_async(*, context: AgentContext) -> CIAnalysis | None:
    """Main entry point for the CI fixer agent (async version)."""
    if context.pr_number is None:
        raise ValueError("pr_number is required in context")

    client = context.gh_client
    pr_number = context.pr_number

    logger.info("Starting CI failure analysis for PR #%d", pr_number)

    # Get PR details
    pr = client.get_pull_request(pr_number)

    # Get failed check runs with details
    try:
        failed_checks = client.get_failed_check_runs(pr_number)
        # Filter out our own agent workflows
        excluded_names = [
            "ci-fixer",
            "ci fixer",
            "ci monitor",
            "trigger review",
            "analyze ci",
            "review pr",
            "fix issues",
        ]
        failed_checks = [
            c for c in failed_checks if not any(excl in c.name.lower() for excl in excluded_names)
        ]

        if not failed_checks:
            logger.info("No failed checks found for PR #%d", pr_number)
            analysis = CIAnalysis(
                status="NO_ISSUES",
                summary="All CI checks have passed or there are no check failures to analyze.",
                failed_checks=[],
                root_causes=[],
                suggestions=[],
            )
            return analysis

        failed_checks_info = _format_all_failures(failed_checks)
        annotations_info = _format_annotations(failed_checks)
        logger.info("Found %d failed checks to analyze", len(failed_checks))
    except Exception as e:
        logger.warning("Failed to get CI status: %s", e)
        failed_checks_info = f"Could not fetch CI status: {e}"
        annotations_info = ""
        failed_checks = []

    # Get workflow logs
    try:
        token = os.getenv("GH_TOKEN", "")
        logs_by_workflow = client.get_failed_workflow_logs(pr_number, token=token)
        workflow_logs_info = _format_workflow_logs(logs_by_workflow)
        logger.info("Fetched logs from %d failed workflows", len(logs_by_workflow))
    except Exception as e:
        logger.warning("Failed to get workflow logs: %s", e)
        workflow_logs_info = f"Could not fetch workflow logs: {e}"

    # Get changed files for context
    try:
        files = client.get_pull_request_files(pr_number)
        diff_context = _format_diff_context(files)
    except Exception as e:
        logger.warning("Failed to get PR diff: %s", e)
        diff_context = "Could not fetch changed files."

    # Build the analysis prompt with all gathered information
    prompt = build_ci_analysis_prompt(
        pr_title=pr.title,
        pr_body=pr.body or "",
        diff_context=diff_context,
        failed_checks_info=failed_checks_info,
        annotations_info=annotations_info,
        workflow_logs_info=workflow_logs_info,
    )

    # Run the CI analyzer agent (single turn, no tools)
    analysis = await run_ci_analysis_async(prompt, context)

    # Populate failed_checks from actual data if agent didn't
    if not analysis.failed_checks and failed_checks:
        analysis = CIAnalysis(
            status=analysis.status,
            summary=analysis.summary,
            failed_checks=[c.name for c in failed_checks],
            root_causes=analysis.root_causes,
            suggestions=analysis.suggestions,
        )

    # Post the analysis comment
    comment = _format_analysis_comment(analysis, pr.url)
    client.comment_pull_request(pr.number, comment)

    # Write to GitHub Actions summary
    _write_actions_summary(analysis, pr.url)

    logger.info("CI analysis completed: %s", analysis.status)
    return analysis


def run_ci_fixer(*, context: AgentContext) -> CIAnalysis | None:
    """Synchronous wrapper for run_ci_fixer_async."""
    return asyncio.run(run_ci_fixer_async(context=context))


def main() -> int:
    """CLI entry point for the CI fixer agent."""
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

    result = run_ci_fixer(context=context)

    # Return non-zero if we couldn't analyze
    if result and result.status == "UNABLE_TO_ANALYZE":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

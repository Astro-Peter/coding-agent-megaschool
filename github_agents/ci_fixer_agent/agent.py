"""CI Fixer Agent using OpenAI Agents SDK.

This agent triggers on CI check failures and analyzes what went wrong,
posting detailed comments with suggestions for how to fix the issues.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agents import Agent, Runner, RunHooks, Tool
from agents.run_context import RunContextWrapper

from github_agents.common.code_index import CodeIndex
from github_agents.common.config import get_pr_number, load_config
from github_agents.common.context import AgentContext
from github_agents.common.github_client import CheckRunData
from github_agents.common.sdk_config import configure_sdk, get_model_name
from github_agents.common.tools import get_ci_fixer_tools

logger = logging.getLogger(__name__)


class ToolLoggingHooks(RunHooks[AgentContext]):
    """Hooks to log tool calls for debugging and observability."""

    async def on_tool_start(
        self,
        context: RunContextWrapper[AgentContext],
        agent: Agent[AgentContext],
        tool: Tool,
    ) -> None:
        """Log when a tool is about to be called."""
        logger.info(
            "Tool call started: %s (agent=%s)",
            tool.name,
            agent.name,
        )

    async def on_tool_end(
        self,
        context: RunContextWrapper[AgentContext],
        agent: Agent[AgentContext],
        tool: Tool,
        result: str,
    ) -> None:
        """Log when a tool call completes."""
        # Truncate long results for readability
        result_preview = result[:200] + "..." if len(result) > 200 else result
        logger.info(
            "Tool call completed: %s (agent=%s) -> %s",
            tool.name,
            agent.name,
            result_preview,
        )


# Marker for machine-readable CI analysis
CI_FIXER_MARKER = "<!-- ci-fixer-agent-report -->"


class CIFixSuggestion(BaseModel):
    """A single suggestion for fixing a CI issue."""
    file: str = Field(description="The file path where the issue occurs")
    line: int | None = Field(default=None, description="Line number if applicable")
    issue: str = Field(description="Description of the issue")
    suggestion: str = Field(description="Suggested fix or action to take")
    code_example: str | None = Field(default=None, description="Example code fix if applicable")


class CIAnalysis(BaseModel):
    """Structured output from the CI fixer agent."""
    status: Literal["ANALYZED", "NO_ISSUES", "UNABLE_TO_ANALYZE"] = Field(
        description="Analysis status"
    )
    summary: str = Field(description="Brief overall summary of the CI failures")
    failed_checks: list[str] = Field(
        default_factory=list,
        description="Names of the checks that failed"
    )
    root_causes: list[str] = Field(
        default_factory=list,
        description="Identified root causes of the failures"
    )
    suggestions: list[CIFixSuggestion] = Field(
        default_factory=list,
        description="Specific suggestions for fixing each issue"
    )
    general_advice: list[str] = Field(
        default_factory=list,
        description="General advice for preventing similar issues"
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
        lines.extend([
            "### Failed Checks",
            *[f"- ❌ {check}" for check in analysis.failed_checks],
            "",
        ])
    
    if analysis.root_causes:
        lines.extend([
            "### Root Causes",
            *[f"- {cause}" for cause in analysis.root_causes],
            "",
        ])
    
    if analysis.suggestions:
        lines.extend([
            "### Suggested Fixes",
            "",
        ])
        for i, sugg in enumerate(analysis.suggestions, 1):
            lines.append(f"**{i}. {sugg.file}**")
            if sugg.line:
                lines.append(f"   - Line: {sugg.line}")
            lines.append(f"   - Issue: {sugg.issue}")
            lines.append(f"   - Fix: {sugg.suggestion}")
            if sugg.code_example:
                lines.append(f"   ```")
                lines.append(f"   {sugg.code_example}")
                lines.append(f"   ```")
            lines.append("")
    
    if analysis.general_advice:
        lines.extend([
            "### General Advice",
            *[f"- 💡 {advice}" for advice in analysis.general_advice],
            "",
        ])
    
    lines.extend([
        "---",
        "*This analysis was generated automatically by the CI Fixer Agent.*",
    ])
    
    # Add machine-readable data
    data_block = {
        "status": analysis.status,
        "failed_checks": analysis.failed_checks,
        "root_causes": analysis.root_causes,
        "suggestion_count": len(analysis.suggestions),
    }
    lines.extend([
        "",
        "<details>",
        "<summary>Machine-readable data</summary>",
        "",
        "```json",
        json.dumps(data_block, indent=2),
        "```",
        "",
        "</details>",
    ])
    
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
        lines.extend([
            "## Failed Checks",
            "| Check Name | Status |",
            "|------------|--------|",
            *[f"| {check} | ❌ Failed |" for check in analysis.failed_checks],
            "",
        ])
    
    if analysis.suggestions:
        lines.extend([
            "## Suggested Fixes",
            "",
        ])
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

def _build_ci_fixer_instructions(
    pr_title: str,
    pr_body: str,
    failed_checks_info: str,
    diff_context: str,
) -> str:
    """Build the instructions for the CI fixer agent."""
    return f"""You are an expert CI/CD debugging assistant. Your task is to analyze CI check failures 
and provide actionable suggestions for how to fix them.

## Pull Request Information

**Title:** {pr_title}

**Description:**
{pr_body}

## Changed Files
{diff_context}

## CI Check Failures (Summary)
{failed_checks_info}

## Your Workflow

Use the CI tools to gather error information, then analyze and provide suggestions.

### Available Tools

1. **`get_check_annotations`** - Get structured error messages with file paths and line numbers 
   from linters and test frameworks. Start here.

2. **`list_failed_workflows`** - Get the list of failed workflow runs and their IDs.

3. **`get_workflow_logs(workflow_run_id, job_name_filter="")`** - Get log output for a workflow run.
   - `workflow_run_id`: Required. Get this from `list_failed_workflows`.
   - `job_name_filter`: Optional. Filter by JOB name (e.g. "build (3.10)"), NOT step name.
     Leave empty to get all logs. If unsure, omit this parameter.

4. **`get_workflow_jobs`** - See which specific jobs and steps failed. Use this to get exact job names
   if you need to filter logs.

### Recommended Approach

1. Call `get_check_annotations` first - this often has structured error info with file paths and line numbers
2. Call `list_failed_workflows` to get workflow IDs
3. Call `get_workflow_logs(workflow_run_id)` WITHOUT a filter to get all logs
4. Analyze the errors and provide your suggestions

## Focus Areas

- Syntax errors and typos
- Import/dependency issues  
- Type errors (for typed languages)
- Test failures and assertions
- Linting violations
- Build configuration problems

Be specific in your suggestions. Include file paths, line numbers, and code snippets.
"""


def _build_ci_fixer_agent(
    pr_title: str,
    pr_body: str,
    failed_checks_info: str,
    diff_context: str,
) -> Agent[AgentContext]:
    """Build the CI fixer agent with dynamic instructions."""
    instructions = _build_ci_fixer_instructions(
        pr_title=pr_title,
        pr_body=pr_body,
        failed_checks_info=failed_checks_info,
        diff_context=diff_context,
    )
    
    return Agent[AgentContext](
        name="CIFixer",
        model=get_model_name(),
        instructions=instructions,
        tools=get_ci_fixer_tools(),
        output_type=CIAnalysis,
    )


# --- Agent Execution ---

async def run_ci_fixer_agent_async(
    pr_title: str,
    pr_body: str,
    failed_checks_info: str,
    diff_context: str,
    context: AgentContext,
) -> CIAnalysis:
    """Run the CI fixer agent and return the analysis."""
    agent = _build_ci_fixer_agent(
        pr_title=pr_title,
        pr_body=pr_body,
        failed_checks_info=failed_checks_info,
        diff_context=diff_context,
    )
    
    try:
        result = await Runner.run(
            agent,
            "Please analyze the CI failures and provide suggestions for fixing them.",
            context=context,
            max_turns=15,
            hooks=ToolLoggingHooks(),
        )
        return result.final_output_as(CIAnalysis)
    except Exception as exc:
        logger.exception("CI Fixer agent failed: %s", exc)
        return CIAnalysis(
            status="UNABLE_TO_ANALYZE",
            summary=f"Failed to analyze CI failures: {exc}",
            failed_checks=[],
            root_causes=[],
            suggestions=[],
            general_advice=["Please check the CI logs manually."],
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
            "ci-fixer", "ci fixer", "ci monitor", 
            "trigger review", "analyze ci", "review pr", 
            "fix issues"
        ]
        failed_checks = [
            c for c in failed_checks 
            if not any(excl in c.name.lower() for excl in excluded_names)
        ]
        
        if not failed_checks:
            logger.info("No failed checks found for PR #%d", pr_number)
            # Post a brief comment indicating no issues
            analysis = CIAnalysis(
                status="NO_ISSUES",
                summary="All CI checks have passed or there are no check failures to analyze.",
                failed_checks=[],
                root_causes=[],
                suggestions=[],
                general_advice=[],
            )
            return analysis
        
        failed_checks_info = _format_all_failures(failed_checks)
        logger.info("Found %d failed checks to analyze", len(failed_checks))
    except Exception as e:
        logger.warning("Failed to get CI status: %s", e)
        failed_checks_info = f"Could not fetch CI status: {e}"
        failed_checks = []
    
    # Get changed files for context
    try:
        files = client.get_pull_request_files(pr_number)
        diff_context = _format_diff_context(files)
    except Exception as e:
        logger.warning("Failed to get PR diff: %s", e)
        diff_context = "Could not fetch changed files."
    
    # Build code index if workspace is available
    workspace_root = context.workspace or Path(os.getenv("GITHUB_WORKSPACE", os.getcwd()))
    index = CodeIndex(str(workspace_root))
    index.build()
    context.workspace = workspace_root
    context.index = index
    
    # Run the CI fixer agent (it will fetch logs on-demand using tools)
    analysis = await run_ci_fixer_agent_async(
        pr_title=pr.title,
        pr_body=pr.body,
        failed_checks_info=failed_checks_info,
        diff_context=diff_context,
        context=context,
    )
    
    # Populate failed_checks from actual data if agent didn't
    if not analysis.failed_checks and failed_checks:
        analysis = CIAnalysis(
            status=analysis.status,
            summary=analysis.summary,
            failed_checks=[c.name for c in failed_checks],
            root_causes=analysis.root_causes,
            suggestions=analysis.suggestions,
            general_advice=analysis.general_advice,
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

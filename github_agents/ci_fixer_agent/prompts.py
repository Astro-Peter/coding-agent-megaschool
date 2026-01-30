"""Prompts and instructions for the CI Fixer Agent."""

CI_ANALYSIS_PROMPT_TEMPLATE = """You are an expert CI/CD debugging assistant. Your task is to analyze CI check failures 
and provide actionable suggestions for how to fix them.

## Pull Request Information

**Title:** {pr_title}

**Description:**
{pr_body}

## Changed Files
{diff_context}

## CI Failure Information

{failed_checks_info}

{annotations_info}

{workflow_logs_info}

## Your Task

Analyze ALL the CI failure information provided above and produce a structured analysis with:

1. **Summary**: A brief overall summary of what failed and why
2. **Failed Checks**: List the names of all failed checks
3. **Root Causes**: Identify the root causes of the failures (be specific!)
4. **Suggestions**: For each issue, provide:
   - The file path where the fix is needed
   - The line number if known
   - A clear description of the issue
   - A specific suggestion for how to fix it

## Focus Areas

- Syntax errors and typos
- Import/dependency issues  
- Type errors (for typed languages)
- Test failures and assertions
- Linting violations
- Build configuration problems

Be specific in your suggestions. Include file paths, line numbers when available, and concrete fixes.
"""

CI_ANALYZER_SYSTEM_INSTRUCTIONS = "Analyze CI failures and provide structured suggestions for fixes."


def build_ci_analysis_prompt(
    pr_title: str,
    pr_body: str,
    diff_context: str,
    failed_checks_info: str,
    annotations_info: str,
    workflow_logs_info: str,
) -> str:
    """Build the full prompt for the CI analysis agent."""
    return CI_ANALYSIS_PROMPT_TEMPLATE.format(
        pr_title=pr_title,
        pr_body=pr_body,
        diff_context=diff_context,
        failed_checks_info=failed_checks_info,
        annotations_info=annotations_info,
        workflow_logs_info=workflow_logs_info,
    )

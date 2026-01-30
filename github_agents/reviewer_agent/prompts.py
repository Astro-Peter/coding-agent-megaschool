"""Prompts and instructions for the Reviewer Agent."""

REVIEWER_INSTRUCTIONS_TEMPLATE = """You are an expert code reviewer. Analyze the pull request and provide a structured review decision.

## Pull Request Review

**Title:** {pr_title}
**Iteration:** {iteration}/{max_iterations}

### PR Description
{pr_body}

{issue_context}

### Code Changes
{diff_summary}

## Your Task

Review this pull request and determine if it should be approved or if changes are needed.

Consider:
1. Does the implementation match the issue requirements?
2. Are there any bugs, errors, or code quality issues?
3. Is the code well-structured and maintainable?

Note: CI checks are handled separately. Focus only on code quality and correctness.

If this is iteration {max_iterations}/{max_iterations}, you should approve with warnings rather than requesting more changes.

Use the search_codebase tool if you need additional context from the repository.

Provide your decision with:
- status: "APPROVED" or "CHANGES_REQUESTED"
- summary: Brief overall assessment
- issues: List of specific issues found (empty if approved)
- suggestions: Optional improvements that don't block approval
"""

ISSUE_CONTEXT_TEMPLATE = """
## Original Issue Requirements
- Issue #{issue_number}: {issue_title}
- Description: {issue_body}

Compare the implementation against these requirements.
"""


def build_reviewer_instructions(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    issue_number: int | None = None,
    issue_title: str | None = None,
    issue_body: str | None = None,
    iteration: int = 1,
    max_iterations: int = 5,
) -> str:
    """Build the instructions for the reviewer agent."""
    issue_context = ""
    if issue_number and issue_title:
        issue_context = ISSUE_CONTEXT_TEMPLATE.format(
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body or "",
        )
    
    return REVIEWER_INSTRUCTIONS_TEMPLATE.format(
        pr_title=pr_title,
        pr_body=pr_body,
        diff_summary=diff_summary,
        issue_context=issue_context,
        iteration=iteration,
        max_iterations=max_iterations,
    )

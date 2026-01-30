"""Prompts and instructions for the Coder Agent."""

CODER_BASE_INSTRUCTIONS = """You are an expert coding agent. Your task is to implement code changes based on a plan.

## Issue
Title: {issue_title}
Body: {issue_body}
{mode_context}
## Plan
Summary: {plan_summary}
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
- If there is CI or reviewer feedback, address it FIRST before other changes.
"""

CODER_PR_COMMENTS_INSTRUCTIONS = """You are an expert coding agent. Your task is to address feedback from PR comments.

## Pull Request Context
PR Title: {pr_title}
PR Body: {pr_body}
Branch: {branch_name}
{mode_context}
## Comment History
The following comments have been made on this PR (oldest first):
{comment_history}
{iteration_note}{feedback_section}
## Instructions
1. First, explore the codebase using list_dir and read_file to understand the current state.
2. Analyze the comment history to understand what changes are requested.
3. Implement the requested changes using write_file, create_file, replace_in_file, etc.
4. When you have addressed ALL feedback, call mark_complete with a summary of what you did.

## Rules
- Always read a file before modifying it.
- Make minimal, focused changes that directly address the feedback.
- Follow existing code style and conventions.
- Do not create unnecessary files.
- Prioritize the most recent comments as they may supersede earlier ones.
- If you cannot address specific feedback, explain why in your mark_complete summary.
- You are committing into the existing PR branch - no new branch creation needed.
"""

CI_FIX_MODE_CONTEXT = """
## Mode: CI Fix
You are running in CI fix mode. Your primary goal is to fix CI failures.
After fixing, the CI will run again automatically.
"""

CI_FEEDBACK_SECTION_TEMPLATE = """
## CI Failure Analysis (CRITICAL - Fix these first!)
The CI checks have failed. The CI Fixer agent identified the following issues:
{ci_items}

You MUST fix these CI issues before the code can be merged.
Focus on:
- Syntax errors and typos
- Import/dependency issues
- Type errors
- Linting violations
- Test failures
You must fix CI failures in code, and not by changing the CI configuration, but by actually looking at the code and fixing the issues.

"""

REVIEWER_FEEDBACK_SECTION_TEMPLATE = """
## Reviewer Feedback (PRIORITY - Address these issues!)
This is iteration {iteration}/{max_iterations}. The reviewer found the following issues:
{feedback_items}

You MUST address these issues before proceeding with any other changes.
"""

ITERATION_NOTE_TEMPLATE = """
## Iteration Note
This is iteration {iteration}/{max_iterations} of the development cycle.
Previous attempts had issues that need to be fixed.
"""


def build_coder_instructions(
    issue_title: str,
    issue_body: str,
    plan_summary: str,
    steps: list[str],
    iteration: int,
    max_iterations: int,
    reviewer_feedback: list[str] | None = None,
    ci_feedback: list[str] | None = None,
    is_ci_fix_mode: bool = False,
) -> str:
    """Build the instructions for the coder agent."""
    steps_text = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(steps))

    feedback_section = ""
    
    # CI feedback takes priority when in CI fix mode
    if ci_feedback and is_ci_fix_mode:
        ci_items = "\n".join(f"  - {item}" for item in ci_feedback)
        feedback_section = CI_FEEDBACK_SECTION_TEMPLATE.format(ci_items=ci_items)
    
    if reviewer_feedback:
        feedback_items = "\n".join(f"  - {item}" for item in reviewer_feedback)
        feedback_section += REVIEWER_FEEDBACK_SECTION_TEMPLATE.format(
            iteration=iteration,
            max_iterations=max_iterations,
            feedback_items=feedback_items,
        )

    iteration_note = ""
    if iteration > 1:
        iteration_note = ITERATION_NOTE_TEMPLATE.format(
            iteration=iteration,
            max_iterations=max_iterations,
        )

    mode_context = CI_FIX_MODE_CONTEXT if is_ci_fix_mode else ""

    return CODER_BASE_INSTRUCTIONS.format(
        issue_title=issue_title,
        issue_body=issue_body,
        mode_context=mode_context,
        plan_summary=plan_summary,
        steps_text=steps_text,
        iteration_note=iteration_note,
        feedback_section=feedback_section,
    )


def build_coder_pr_comments_instructions(
    pr_title: str,
    pr_body: str,
    branch_name: str,
    comment_history: list[dict],
    iteration: int = 1,
    max_iterations: int = 5,
    reviewer_feedback: list[str] | None = None,
    ci_feedback: list[str] | None = None,
    is_ci_fix_mode: bool = False,
) -> str:
    """Build instructions for the coder agent when working from PR comment history.
    
    Args:
        pr_title: The PR title.
        pr_body: The PR body/description.
        branch_name: The branch to commit into.
        comment_history: List of comment dicts with 'author', 'body', 'created_at' keys.
        iteration: Current iteration number.
        max_iterations: Maximum allowed iterations.
        reviewer_feedback: Optional list of reviewer feedback items.
        ci_feedback: Optional list of CI feedback items.
        is_ci_fix_mode: Whether we're in CI fix mode.
    
    Returns:
        Formatted instruction string for the coder agent.
    """
    # Format comment history
    comment_lines = []
    for comment in comment_history:
        author = comment.get('author', 'Unknown')
        body = comment.get('body', '')
        created_at = comment.get('created_at', '')
        comment_lines.append(f"**{author}** ({created_at}):\n{body}\n")
    
    comment_history_text = "\n---\n".join(comment_lines) if comment_lines else "No comments yet."

    feedback_section = ""
    
    # CI feedback takes priority when in CI fix mode
    if ci_feedback and is_ci_fix_mode:
        ci_items = "\n".join(f"  - {item}" for item in ci_feedback)
        feedback_section = CI_FEEDBACK_SECTION_TEMPLATE.format(ci_items=ci_items)
    
    if reviewer_feedback:
        feedback_items = "\n".join(f"  - {item}" for item in reviewer_feedback)
        feedback_section += REVIEWER_FEEDBACK_SECTION_TEMPLATE.format(
            iteration=iteration,
            max_iterations=max_iterations,
            feedback_items=feedback_items,
        )

    iteration_note = ""
    if iteration > 1:
        iteration_note = ITERATION_NOTE_TEMPLATE.format(
            iteration=iteration,
            max_iterations=max_iterations,
        )

    mode_context = CI_FIX_MODE_CONTEXT if is_ci_fix_mode else ""

    return CODER_PR_COMMENTS_INSTRUCTIONS.format(
        pr_title=pr_title,
        pr_body=pr_body,
        branch_name=branch_name,
        mode_context=mode_context,
        comment_history=comment_history_text,
        iteration_note=iteration_note,
        feedback_section=feedback_section,
    )

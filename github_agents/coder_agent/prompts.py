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

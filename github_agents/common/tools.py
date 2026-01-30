"""Shared tools for agents using the OpenAI Agents SDK."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from github_agents.common.context import AgentContext


# --- File System Tools ---

@function_tool
def get_workdir(ctx: RunContextWrapper[AgentContext]) -> str:
    """Return the current workspace root directory path."""
    workspace = ctx.context.workspace
    if workspace is None:
        return "No workspace configured"
    return str(workspace)


@function_tool
def list_dir(ctx: RunContextWrapper[AgentContext], path: str = ".") -> dict[str, Any]:
    """List files and folders under a directory.
    
    Args:
        path: Relative path to list. Defaults to current directory.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    # Security: ensure we don't escape workspace
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    if not target.exists() or not target.is_dir():
        return {"ok": False, "message": "Path does not exist or is not a directory"}
    
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        entries.append({
            "name": item.name,
            "path": str(item.relative_to(workspace)),
            "type": "dir" if item.is_dir() else "file",
        })
    
    return {"ok": True, "entries": entries}


@function_tool
def read_file(ctx: RunContextWrapper[AgentContext], path: str) -> dict[str, Any]:
    """Read a UTF-8 text file from the workspace and return its content.
    
    Args:
        path: Relative path to the file to read.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    if not target.exists() or not target.is_file():
        return {"ok": False, "message": "Path does not exist or is not a file"}
    
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "message": "File is not valid UTF-8"}
    
    return {"ok": True, "content": content}


@function_tool
def create_file(ctx: RunContextWrapper[AgentContext], path: str, content: str) -> dict[str, Any]:
    """Create a new file with the provided content. Fails if file already exists.
    
    Args:
        path: Relative path for the new file.
        content: Content to write to the file.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    if target.exists():
        return {"ok": False, "message": "File already exists"}
    
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    
    return {"ok": True, "message": "File created", "path": str(target.relative_to(workspace))}


@function_tool
def write_file(ctx: RunContextWrapper[AgentContext], path: str, content: str) -> dict[str, Any]:
    """Overwrite a file with the provided content. Creates the file if it doesn't exist.
    
    Args:
        path: Relative path to the file.
        content: Content to write to the file.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    
    return {"ok": True, "message": "File written", "path": str(target.relative_to(workspace))}


@function_tool
def append_file(ctx: RunContextWrapper[AgentContext], path: str, content: str) -> dict[str, Any]:
    """Append content to the end of a file. Appends from a new line.
    
    Args:
        path: Relative path to the file.
        content: Content to append.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{content}")
    
    return {"ok": True, "message": "Content appended", "path": str(target.relative_to(workspace))}


@function_tool
def replace_in_file(
    ctx: RunContextWrapper[AgentContext], path: str, old: str, new: str
) -> dict[str, Any]:
    """Find and replace a substring in a file. Replaces all occurrences.
    
    Args:
        path: Relative path to the file.
        old: Text to find.
        new: Text to replace with.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    if not target.exists() or not target.is_file():
        return {"ok": False, "message": "Path does not exist or is not a file"}
    
    content = target.read_text(encoding="utf-8")
    if old not in content:
        return {"ok": False, "message": "Old text not found"}
    
    target.write_text(content.replace(old, new), encoding="utf-8")
    
    return {"ok": True, "message": "Content replaced", "path": str(target.relative_to(workspace))}


@function_tool
def delete_file(ctx: RunContextWrapper[AgentContext], path: str) -> dict[str, Any]:
    """Delete a file from the workspace. Cannot delete directories.
    
    Args:
        path: Relative path to the file to delete.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    if not target.exists():
        return {"ok": False, "message": "Path does not exist"}
    
    if target.is_dir():
        return {"ok": False, "message": "Refusing to delete a directory"}
    
    target.unlink()
    
    return {"ok": True, "message": "File deleted", "path": str(target.relative_to(workspace))}


@function_tool
def make_dir(ctx: RunContextWrapper[AgentContext], path: str) -> dict[str, Any]:
    """Create a directory (and any necessary parent directories) in the workspace.
    
    Args:
        path: Relative path for the directory to create.
    """
    workspace = ctx.context.workspace
    if workspace is None:
        return {"ok": False, "message": "No workspace configured"}
    
    target = (workspace / path).resolve()
    
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "message": "Path escapes the workspace root"}
    
    target.mkdir(parents=True, exist_ok=True)
    
    return {"ok": True, "message": "Directory created", "path": str(target.relative_to(workspace))}


# --- Code Search Tools ---

@function_tool
def search_codebase(
    ctx: RunContextWrapper[AgentContext], query: str, max_results: int = 6
) -> dict[str, Any]:
    """Search the codebase for a query string. Returns matching files with snippets.
    
    Args:
        query: Text to search for in the codebase.
        max_results: Maximum number of results to return (1-20).
    """
    index = ctx.context.index
    if index is None:
        return {"ok": False, "message": "No code index available"}
    
    max_results = max(1, min(20, max_results))
    results = index.search(query, max_results=max_results)
    
    return {"ok": True, "query": query, "results": results}


# --- Completion Tool ---

@function_tool
def mark_complete(ctx: RunContextWrapper[AgentContext], summary: str) -> str:
    """Signal that you have finished implementing all changes. Call this when done.
    
    Args:
        summary: Brief summary of what was implemented.
    """
    return f"COMPLETE: {summary}"


# --- CI/Workflow Tools ---

@function_tool
def list_failed_workflows(ctx: RunContextWrapper[AgentContext]) -> dict[str, Any]:
    """List all failed workflow runs for the current PR.
    
    Returns a list of failed workflows with their IDs, names, and conclusions.
    Use get_workflow_logs to fetch detailed logs for a specific workflow.
    """
    pr_number = ctx.context.pr_number
    if pr_number is None:
        return {"ok": False, "message": "No PR number in context"}
    
    client = ctx.context.gh_client
    
    try:
        failed_runs = client.get_failed_workflow_runs(pr_number)
        
        if not failed_runs:
            return {
                "ok": True, 
                "message": "No failed workflows found",
                "workflows": []
            }
        
        workflows = []
        for run in failed_runs:
            workflows.append({
                "id": run.id,
                "name": run.name,
                "conclusion": run.conclusion,
                "url": run.html_url,
            })
        
        return {"ok": True, "workflows": workflows}
    except Exception as e:
        return {"ok": False, "message": f"Failed to get workflows: {e}"}


@function_tool
def get_workflow_jobs(ctx: RunContextWrapper[AgentContext], workflow_run_id: int) -> dict[str, Any]:
    """Get jobs for a specific workflow run.
    
    Args:
        workflow_run_id: The ID of the workflow run (from list_failed_workflows).
        
    Returns a list of jobs with their names, statuses, and step information.
    """
    client = ctx.context.gh_client
    
    try:
        jobs = client.get_workflow_run_jobs(workflow_run_id)
        
        job_list = []
        for job in jobs:
            job_info = {
                "id": job.id,
                "name": job.name,
                "status": job.status,
                "conclusion": job.conclusion,
            }
            
            # Include failed steps if available
            if job.steps:
                failed_steps = [
                    s for s in job.steps 
                    if s.get("conclusion") not in ("success", "skipped", None)
                ]
                if failed_steps:
                    job_info["failed_steps"] = [
                        {"name": s["name"], "conclusion": s.get("conclusion")}
                        for s in failed_steps
                    ]
            
            job_list.append(job_info)
        
        return {"ok": True, "jobs": job_list}
    except Exception as e:
        return {"ok": False, "message": f"Failed to get jobs: {e}"}


@function_tool
def get_workflow_logs(
    ctx: RunContextWrapper[AgentContext], 
    workflow_run_id: int,
    job_name_filter: str = "",
) -> dict[str, Any]:
    """Get logs for a specific workflow run.
    
    Args:
        workflow_run_id: The ID of the workflow run (from list_failed_workflows).
        job_name_filter: Optional filter by JOB name (e.g. "build (3.10)"), NOT step name.
            Leave empty to get logs from all jobs. Job names come from get_workflow_jobs.
        
    Returns parsed log data including extracted error lines and log content.
    """
    client = ctx.context.gh_client
    token = os.getenv("GH_TOKEN", "")
    
    try:
        logs = client.download_workflow_run_logs(workflow_run_id, token=token)
        
        if not logs:
            return {"ok": False, "message": "No logs found for this workflow run"}
        
        # Collect all job names for error messages
        all_job_names = [l.job_name for l in logs]
        
        # Filter by job name if specified
        if job_name_filter:
            filtered_logs = [l for l in logs if job_name_filter.lower() in l.job_name.lower()]
            if not filtered_logs:
                return {
                    "ok": False, 
                    "message": f"No jobs matching filter '{job_name_filter}'",
                    "available_jobs": all_job_names,
                    "hint": "Use one of the available_jobs names, or omit the filter to get all logs"
                }
            logs = filtered_logs
        
        result = []
        for log in logs:
            log_entry = {
                "job_name": log.job_name,
                "error_count": len(log.error_lines),
                "errors": log.error_lines[:50],  # Limit to 50 errors
            }
            
            # Include truncated log content
            content = log.log_content
            if len(content) > 5000:
                # Show last 5000 chars (usually has the errors)
                content = "... (earlier output truncated) ...\n" + content[-5000:]
            log_entry["log_content"] = content
            
            result.append(log_entry)
        
        return {"ok": True, "logs": result}
    except Exception as e:
        return {"ok": False, "message": f"Failed to get logs: {e}"}


@function_tool
def get_check_annotations(ctx: RunContextWrapper[AgentContext]) -> dict[str, Any]:
    """Get check run annotations (structured error messages) for the current PR.
    
    Returns annotations from check runs, which often include file paths, 
    line numbers, and specific error messages from linters and test frameworks.
    """
    pr_number = ctx.context.pr_number
    if pr_number is None:
        return {"ok": False, "message": "No PR number in context"}
    
    client = ctx.context.gh_client
    
    try:
        check_runs = client.get_failed_check_runs(pr_number)
        
        all_annotations = []
        for check in check_runs:
            if check.annotations:
                for ann in check.annotations:
                    all_annotations.append({
                        "check_name": check.name,
                        "file": ann.path,
                        "line": ann.start_line,
                        "level": ann.annotation_level,
                        "message": ann.message,
                        "title": ann.title,
                    })
        
        if not all_annotations:
            return {
                "ok": True, 
                "message": "No annotations found (try get_workflow_logs for full logs)",
                "annotations": []
            }
        
        return {"ok": True, "annotations": all_annotations}
    except Exception as e:
        return {"ok": False, "message": f"Failed to get annotations: {e}"}


# --- Tool Collections ---

def get_file_tools() -> list:
    """Get all file system tools."""
    return [
        get_workdir,
        list_dir,
        read_file,
        create_file,
        write_file,
        append_file,
        replace_in_file,
        delete_file,
        make_dir,
    ]


def get_coder_tools() -> list:
    """Get all tools for the coder agent."""
    return get_file_tools() + [search_codebase, mark_complete]


def get_reviewer_tools() -> list:
    """Get tools for the reviewer agent."""
    return [search_codebase]


def get_ci_fixer_tools() -> list:
    """Get tools for the CI fixer agent (CI log access only).
    
    The CI fixer agent only needs CI tools to analyze failures.
    File exploration is not needed - the CI tools provide file paths
    and line numbers in error messages.
    """
    return [
        get_check_annotations,
        list_failed_workflows,
        get_workflow_jobs,
        get_workflow_logs,
    ]

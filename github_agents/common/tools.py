"""Shared tools for agents using the OpenAI Agents SDK."""
from __future__ import annotations

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
    """Append content to the end of a file.
    
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
        handle.write("\n" + content)
    
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

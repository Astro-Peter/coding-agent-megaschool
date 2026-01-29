from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def as_openai_tool(self) -> dict[str, Any]:
        """Return tool in Chat Completions format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {"ok": self.ok, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self._tools.values()]

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(ok=False, message=f"Unknown tool: {name}").as_dict()
        return tool.handler(arguments)


class FileWorkspace:
    def __init__(self, workdir: str | Path) -> None:
        self._workdir = Path(workdir).resolve()
        self._root = self._workdir

    def get_workdir(self) -> str:
        return str(self._workdir)

    def get_root(self) -> str:
        return str(self._root)

    def set_workdir(self, relative_path: str) -> dict[str, Any]:
        if Path(relative_path).is_absolute():
            return ToolResult(ok=False, message="workdir must be a relative path.").as_dict()
        try:
            candidate = self._resolve(relative_path)
        except ValueError as exc:
            return ToolResult(ok=False, message=str(exc)).as_dict()
        if not candidate.exists() or not candidate.is_dir():
            return ToolResult(
                ok=False,
                message="workdir does not exist or is not a directory.",
            ).as_dict()
        self._workdir = candidate
        return ToolResult(
            ok=True, message="workdir updated.", data={"workdir": str(self._workdir)}
        ).as_dict()

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self._workdir / relative_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("Path escapes the workspace root.") from exc
        return candidate

    def _resolve_required(self, relative_path: str) -> tuple[Path, ToolResult | None]:
        if Path(relative_path).is_absolute():
            return Path("."), ToolResult(ok=False, message="path must be relative to workdir.")
        try:
            return self._resolve(relative_path), None
        except ValueError as exc:
            return Path("."), ToolResult(ok=False, message=str(exc))

    def list_dir(self, relative_path: str = ".") -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        if not target.exists() or not target.is_dir():
            return ToolResult(
                ok=False, message="path does not exist or is not a directory."
            ).as_dict()
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            entries.append(
                {
                    "name": item.name,
                    "path": str(item.relative_to(self._root)),
                    "type": "dir" if item.is_dir() else "file",
                }
            )
        return ToolResult(ok=True, message="ok", data={"entries": entries}).as_dict()

    def read_file(self, relative_path: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        if not target.exists() or not target.is_file():
            return ToolResult(
                ok=False, message="path does not exist or is not a file."
            ).as_dict()
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, message="file is not valid UTF-8.").as_dict()
        return ToolResult(ok=True, message="ok", data={"content": content}).as_dict()

    def create_file(self, relative_path: str, content: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        if target.exists():
            return ToolResult(ok=False, message="file already exists.").as_dict()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, message="file created.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def write_file(self, relative_path: str, content: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, message="file written.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def append_file(self, relative_path: str, content: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return ToolResult(ok=True, message="content appended.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def replace_in_file(self, relative_path: str, old: str, new: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        if not target.exists() or not target.is_file():
            return ToolResult(
                ok=False, message="path does not exist or is not a file."
            ).as_dict()
        content = target.read_text(encoding="utf-8")
        if old not in content:
            return ToolResult(ok=False, message="old text not found.").as_dict()
        target.write_text(content.replace(old, new), encoding="utf-8")
        return ToolResult(ok=True, message="content replaced.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def delete_path(self, relative_path: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        if not target.exists():
            return ToolResult(ok=False, message="path does not exist.").as_dict()
        if target.is_dir():
            return ToolResult(
                ok=False, message="refusing to delete a directory."
            ).as_dict()
        target.unlink()
        return ToolResult(ok=True, message="file deleted.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def make_dir(self, relative_path: str) -> dict[str, Any]:
        target, error = self._resolve_required(relative_path)
        if error:
            return error.as_dict()
        target.mkdir(parents=True, exist_ok=True)
        return ToolResult(ok=True, message="directory created.", data={"path": str(target.relative_to(self._root))}).as_dict()

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="get_workdir",
                description="Return the current workspace root directory path.",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _args: ToolResult(
                    ok=True, message="ok", data={"workdir": self.get_workdir()}
                ).as_dict(),
            ),
            ToolSpec(
                name="set_workdir",
                description="Change the current working directory to a relative path within the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path to change to."}},
                    "required": ["path"],
                },
                handler=lambda args: self.set_workdir(args.get("path", "")),
            ),
            ToolSpec(
                name="list_dir",
                description="List files and folders under a directory. Returns entries with name, path, and type (file/dir).",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path to list. Defaults to current directory.", "default": "."}},
                    "required": [],
                },
                handler=lambda args: self.list_dir(args.get("path", ".")),
            ),
            ToolSpec(
                name="read_file",
                description="Read a UTF-8 text file from the workspace and return its content.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path to the file to read."}},
                    "required": ["path"],
                },
                handler=lambda args: self.read_file(args.get("path", "")),
            ),
            ToolSpec(
                name="create_file",
                description="Create a new file with the provided content. Fails if file already exists.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path for the new file."},
                        "content": {"type": "string", "description": "Content to write to the file."},
                    },
                    "required": ["path", "content"],
                },
                handler=lambda args: self.create_file(
                    args.get("path", ""), args.get("content", "")
                ),
            ),
            ToolSpec(
                name="write_file",
                description="Overwrite a file with the provided content. Creates the file if it doesn't exist.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file."},
                        "content": {"type": "string", "description": "Content to write to the file."},
                    },
                    "required": ["path", "content"],
                },
                handler=lambda args: self.write_file(
                    args.get("path", ""), args.get("content", "")
                ),
            ),
            ToolSpec(
                name="append_file",
                description="Append content to the end of a file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file."},
                        "content": {"type": "string", "description": "Content to append."},
                    },
                    "required": ["path", "content"],
                },
                handler=lambda args: self.append_file(
                    args.get("path", ""), args.get("content", "")
                ),
            ),
            ToolSpec(
                name="replace_in_file",
                description="Find and replace a substring in a file. Replaces all occurrences.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file."},
                        "old": {"type": "string", "description": "Text to find."},
                        "new": {"type": "string", "description": "Text to replace with."},
                    },
                    "required": ["path", "old", "new"],
                },
                handler=lambda args: self.replace_in_file(
                    args.get("path", ""),
                    args.get("old", ""),
                    args.get("new", ""),
                ),
            ),
            ToolSpec(
                name="delete_file",
                description="Delete a file from the workspace. Cannot delete directories.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path to the file to delete."}},
                    "required": ["path"],
                },
                handler=lambda args: self.delete_path(args.get("path", "")),
            ),
            ToolSpec(
                name="make_dir",
                description="Create a directory (and any necessary parent directories) in the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path for the directory to create."}},
                    "required": ["path"],
                },
                handler=lambda args: self.make_dir(args.get("path", "")),
            ),
        ]


def create_mark_complete_tool(callback: Callable[[str], None]) -> ToolSpec:
    """Create a mark_complete tool that signals the agent is done."""
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        summary = args.get("summary", "Implementation complete.")
        callback(summary)
        return ToolResult(ok=True, message="Marked as complete.", data={"summary": summary}).as_dict()

    return ToolSpec(
        name="mark_complete",
        description="Signal that you have finished implementing all changes. Call this when done.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was implemented.",
                }
            },
            "required": ["summary"],
        },
        handler=handler,
    )

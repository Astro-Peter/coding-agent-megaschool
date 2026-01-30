from __future__ import annotations

import os
from dataclasses import dataclass

_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".idea",
    ".vscode",
    ".cursor",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

_ALLOWED_EXTENSIONS = {
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".scss",
    ".html",
    ".sh",
    ".sql",
}

_MAX_FILE_SIZE_BYTES = 500_000


@dataclass
class IndexedFile:
    path: str
    content: str


class CodeIndex:
    def __init__(self, root: str) -> None:
        self._root = os.path.abspath(root)
        self._files: list[IndexedFile] = []

    def build(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self._root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
            for filename in filenames:
                if not _should_index(filename):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size > _MAX_FILE_SIZE_BYTES:
                    continue
                if not _is_text_file(path):
                    continue
                content = _read_text(path)
                if not content.strip():
                    continue
                rel_path = os.path.relpath(path, self._root)
                self._files.append(IndexedFile(path=rel_path, content=content))

    def search(self, query: str, *, max_results: int = 6) -> list[dict[str, str | int]]:
        query = (query or "").strip()
        if not query:
            return []
        results: list[tuple[int, IndexedFile, dict[str, str | int]]] = []
        query_lower = query.lower()
        for indexed in self._files:
            content_lower = indexed.content.lower()
            score = content_lower.count(query_lower)
            if score <= 0:
                continue
            snippet = _build_snippet(indexed.content, query_lower)
            results.append(
                (
                    score,
                    indexed,
                    {
                        "path": indexed.path,
                        "score": score,
                        "snippet": snippet,
                    },
                )
            )
        results.sort(key=lambda item: (-item[0], item[1].path))
        return [item[2] for item in results[: max_results or 6]]


def _should_index(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    if ext in _ALLOWED_EXTENSIONS:
        return True
    return False


def _is_text_file(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(2048)
        return b"\x00" not in chunk
    except OSError:
        return False


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return ""


def _build_snippet(content: str, query_lower: str) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if query_lower in line.lower():
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            return "\n".join(
                f"{line_index + 1}: {lines[line_index]}" for line_index in range(start, end)
            )
    preview = content[:200].replace("\n", " ")
    return preview

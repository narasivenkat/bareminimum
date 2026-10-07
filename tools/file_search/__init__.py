"""file_search tool — search for files in the workspace matching a pattern."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Dict, List, Set

from pydantic import BaseModel, Field

from tools._common import resolve_path, to_rel

NAME = "file_search"
DESCRIPTION = (
    "Search for files in the workspace matching a glob or wildcard pattern "
    "(e.g., '*.py', '**/*.json', 'test_*')."
)

DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    ".vscode",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
}


class FileSearchArgs(BaseModel):
    pattern: str = Field(
        ...,
        description="Glob pattern or wildcard pattern to match files (e.g., '*.py', '**/*.txt', 'test_*').",
    )
    path: str = Field(
        ".",
        description="Directory path relative to the workspace root to search within.",
    )


PARAMETERS = FileSearchArgs.model_json_schema()


def _matches_pattern(
    rel_workspace_str: str,
    rel_base_str: str,
    file_name: str,
    pattern: str,
) -> bool:
    """Check whether a file matches the search pattern."""
    if Path(rel_workspace_str).match(pattern):
        return True
    if Path(rel_base_str).match(pattern):
        return True
    if Path(file_name).match(pattern):
        return True
    if fnmatch.fnmatch(rel_workspace_str, pattern):
        return True
    if fnmatch.fnmatch(rel_base_str, pattern):
        return True
    if fnmatch.fnmatch(file_name, pattern):
        return True
    return False


def file_search(**kwargs: Any) -> Dict[str, Any]:
    args = FileSearchArgs(**kwargs)
    base_path = resolve_path(args.path, must_exist=True)
    if not base_path.is_dir():
        raise ValueError(f"Not a directory: {args.path!r}")

    matched_files: List[str] = []

    for root, dirs, files in os.walk(base_path):
        # Filter out ignored directories in-place
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]

        for file_name in sorted(files):
            full_path = Path(root) / file_name
            if not full_path.is_file():
                continue

            rel_workspace_str = to_rel(full_path)
            try:
                rel_base_str = full_path.relative_to(base_path).as_posix()
            except ValueError:
                rel_base_str = rel_workspace_str

            if _matches_pattern(rel_workspace_str, rel_base_str, file_name, args.pattern):
                matched_files.append(rel_workspace_str)

    matched_files.sort()

    return {
        "path": to_rel(base_path),
        "pattern": args.pattern,
        "files": matched_files,
        "count": len(matched_files),
    }


# Aliases
search_files = file_search
glob_search = file_search


def register(registry) -> None:
    registry.register(NAME, file_search, description=DESCRIPTION, parameters=PARAMETERS)
    registry.register(
        "search_files",
        search_files,
        description="Alias of file_search: search for files matching a pattern.",
        parameters=PARAMETERS,
    )
    registry.register(
        "glob_search",
        glob_search,
        description="Alias of file_search: search for files matching a pattern.",
        parameters=PARAMETERS,
    )


__all__ = [
    "NAME",
    "DESCRIPTION",
    "PARAMETERS",
    "file_search",
    "search_files",
    "glob_search",
    "register",
]

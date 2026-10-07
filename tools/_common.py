"""Shared helpers for the harness tools.

Provides workspace-scoped path resolution (to prevent path traversal outside
the project), safe file reading, and a directory walker that skips noisy
directories such as ``.git`` and ``.venv``.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    """
    Return the workspace root all tools operate within.

    Defaults to the repository root (the parent of the ``tools`` package) but
    can be overridden with the ``HARNESS_WORKSPACE`` environment variable.
    """
    override = os.environ.get("HARNESS_WORKSPACE")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parent.parent


def resolve_path(path: str, *, must_exist: bool = False) -> Path:
    """
    Resolve ``path`` against the workspace root and refuse to escape it.

    Args:
        path: Absolute or workspace-relative path.
        must_exist: When ``True``, raise ``FileNotFoundError`` if the path
            does not exist.

    Raises:
        ValueError: If the resolved path lies outside the workspace root.
        FileNotFoundError: If ``must_exist`` and the path is missing.
    """
    root = workspace_root()
    given = Path(path)
    candidate = (given if given.is_absolute() else root / given).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace root: {path!r}") from exc

    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"Path not found: {path!r}")
    return candidate


def to_rel(path: Path) -> str:
    """Render ``path`` relative to the workspace root using forward slashes."""
    try:
        return path.resolve().relative_to(workspace_root()).as_posix()
    except ValueError:
        return path.as_posix()

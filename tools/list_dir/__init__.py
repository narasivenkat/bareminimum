"""list_dir tool — list a directory's immediate contents."""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from tools._common import resolve_path, to_rel

NAME = "list_dir"
DESCRIPTION = "List directory contents to understand project structure. Folders end with '/'."


class ListDirArgs(BaseModel):
    path: str = Field(".", description="Directory path relative to the workspace root.")


PARAMETERS = ListDirArgs.model_json_schema()


def list_dir(**kwargs: Any) -> Dict[str, Any]:
    args = ListDirArgs(**kwargs)
    path = resolve_path(args.path, must_exist=True)
    if not path.is_dir():
        raise ValueError(f"Not a directory: {args.path!r}")

    entries = []
    for child in sorted(path.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        entries.append(child.name + ("/" if child.is_dir() else ""))

    return {"path": to_rel(path), "entries": entries, "count": len(entries)}


def register(registry) -> None:
    registry.register(NAME, list_dir, description=DESCRIPTION, parameters=PARAMETERS)


__all__ = ["NAME", "DESCRIPTION", "PARAMETERS", "list_dir", "register"]

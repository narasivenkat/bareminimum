"""read_file tool — return a file's contents, optionally a line range."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from tools._common import resolve_path, to_rel

NAME = "read_file"
DESCRIPTION = (
    "Return a file's contents so the model can see code before editing. "
    "Optionally limit to a 1-based inclusive line range."
)


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="File path relative to the workspace root.")
    start_line: Optional[int] = Field(None, ge=1, description="1-based first line to return.")
    end_line: Optional[int] = Field(None, ge=1, description="1-based last line (inclusive).")


PARAMETERS = ReadFileArgs.model_json_schema()


def read_file(**kwargs: Any) -> Dict[str, Any]:
    args = ReadFileArgs(**kwargs)
    path = resolve_path(args.path, must_exist=True)
    if not path.is_file():
        raise ValueError(f"Not a file: {args.path!r}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = args.start_line or 1
    end = args.end_line or total
    if args.end_line is not None and args.start_line is not None and end < start:
        raise ValueError("end_line must be >= start_line.")

    end = min(end, total)
    selected = lines[start - 1 : end] if start <= total else []

    return {
        "path": to_rel(path),
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "content": "\n".join(selected),
    }


def register(registry) -> None:
    registry.register(NAME, read_file, description=DESCRIPTION, parameters=PARAMETERS)


__all__ = ["NAME", "DESCRIPTION", "PARAMETERS", "read_file", "register"]

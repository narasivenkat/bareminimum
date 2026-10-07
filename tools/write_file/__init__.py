"""write_file / create_file tool — create or overwrite a file with Human-in-the-Loop (HITL) approval and diff preview."""

from __future__ import annotations

import difflib
import sys
from typing import Any, Dict

from pydantic import BaseModel, Field

from tools._common import resolve_path, to_rel

DESCRIPTION = "Create or overwrite a file with Human-in-the-Loop (HITL) approval and diff preview."


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Target file path relative to the workspace root.")
    content: str = Field(..., description="Full file content to write.")
    overwrite: bool = Field(True, description="Overwrite the file if it already exists.")
    interactive: bool = Field(True, description="Require human approval before writing if True.")
    dry_run: bool = Field(False, description="Simulate changes and return diff preview without modifying disk.")


PARAMETERS = WriteFileArgs.model_json_schema()


def write_file(**kwargs: Any) -> Dict[str, Any]:
    args = WriteFileArgs(**kwargs)
    path = resolve_path(args.path)
    existed = path.exists()
    if existed and not args.overwrite:
        raise ValueError(f"File exists and overwrite=False: {args.path!r}")

    old_content = path.read_text(encoding="utf-8", errors="replace") if existed else ""

    # Generate unified diff for developer review
    diff = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            args.content.splitlines(keepends=True),
            fromfile=f"a/{to_rel(path)}",
            tofile=f"b/{to_rel(path)}",
        )
    )
    diff_text = "".join(diff)

    # In dry_run or interactive mode, output diff preview
    if args.dry_run or args.interactive:
        print(f"\n[HITL Approval Gate] Pending Write Operation to '{to_rel(path)}':")
        print("------------------------------------------------------------")
        print(diff_text if diff_text else "[New File Creation / No Changes]")
        print("------------------------------------------------------------")

    if args.dry_run:
        print("[DRY RUN] Operation simulated; no changes written.")
        return {
            "path": to_rel(path),
            "status": "dry_run_simulated",
            "overwritten": existed,
            "diff": diff_text,
            "bytes_written": 0,
        }

    if args.interactive:
        try:
            confirm = input(f"Approve file write to '{to_rel(path)}'? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                raise PermissionError(f"User rejected write operation for '{to_rel(path)}'.")
        except (EOFError, OSError) as exc:
            raise PermissionError(
                f"User rejected write operation for '{to_rel(path)}' (non-interactive input / EOF)."
            ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")

    return {
        "path": to_rel(path),
        "bytes_written": len(args.content.encode("utf-8")),
        "overwritten": existed,
        "diff": diff_text,
        "status": "approved_and_written",
    }


# `create_file` is an alias exposed under its own tool name.
create_file = write_file


def register(registry) -> None:
    registry.register("write_file", write_file, description=DESCRIPTION, parameters=PARAMETERS)
    registry.register(
        "create_file",
        create_file,
        description="Alias of write_file: create or overwrite a file.",
        parameters=PARAMETERS,
    )


__all__ = ["DESCRIPTION", "PARAMETERS", "write_file", "create_file", "register"]

"""delete_file tool — delete a file or directory with Human-in-the-Loop (HITL) approval."""

from __future__ import annotations

import shutil
from typing import Any, Dict

from pydantic import BaseModel, Field

from tools._common import resolve_path, to_rel

NAME = "delete_file"
DESCRIPTION = (
    "Delete a file or directory relative to the workspace root with Human-in-the-Loop (HITL) approval."
)


class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="Target file or directory path relative to the workspace root.")
    recursive: bool = Field(False, description="Recursively delete directory and its contents if True.")
    interactive: bool = Field(True, description="Require human approval before deleting if True.")
    dry_run: bool = Field(False, description="Simulate deletion without modifying disk.")


PARAMETERS = DeleteFileArgs.model_json_schema()


def delete_file(**kwargs: Any) -> Dict[str, Any]:
    args = DeleteFileArgs(**kwargs)
    path = resolve_path(args.path, must_exist=True)

    is_dir = path.is_dir()
    if is_dir and not args.recursive:
        raise ValueError(
            f"Path '{args.path}' is a directory. Set recursive=True to delete a directory."
        )

    if args.dry_run or args.interactive:
        target_type = "directory" if is_dir else "file"
        print(f"\n[HITL Approval Gate] Pending Delete Operation on {target_type} '{to_rel(path)}':")
        print("------------------------------------------------------------")
        print(f"Target: {to_rel(path)}")
        if is_dir:
            print(f"Recursive: {args.recursive}")
        print("------------------------------------------------------------")

    if args.dry_run:
        print("[DRY RUN] Operation simulated; no files deleted.")
        return {
            "path": to_rel(path),
            "status": "dry_run_simulated",
            "deleted": False,
        }

    if args.interactive:
        try:
            confirm = input(f"Approve deletion of '{to_rel(path)}'? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                raise PermissionError(f"User rejected delete operation for '{to_rel(path)}'.")
        except (EOFError, OSError) as exc:
            raise PermissionError(
                f"User rejected delete operation for '{to_rel(path)}' (non-interactive input / EOF)."
            ) from exc

    if is_dir:
        shutil.rmtree(path)
    else:
        path.unlink()

    return {
        "path": to_rel(path),
        "status": "approved_and_deleted",
        "deleted": True,
    }


# Aliases
remove_file = delete_file
delete = delete_file
unlink = delete_file


def register(registry) -> None:
    registry.register(NAME, delete_file, description=DESCRIPTION, parameters=PARAMETERS)
    registry.register(
        "remove_file",
        remove_file,
        description="Alias of delete_file: delete a file or directory.",
        parameters=PARAMETERS,
    )
    registry.register(
        "delete",
        delete,
        description="Alias of delete_file: delete a file or directory.",
        parameters=PARAMETERS,
    )
    registry.register(
        "unlink",
        unlink,
        description="Alias of delete_file: delete a file or directory.",
        parameters=PARAMETERS,
    )


__all__ = [
    "NAME",
    "DESCRIPTION",
    "PARAMETERS",
    "delete_file",
    "remove_file",
    "delete",
    "unlink",
    "register",
]

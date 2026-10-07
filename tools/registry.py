"""Tool registry and dispatch.

This module owns the tool abstraction shared by the agent loop and the tool
packages:

* :class:`ToolSpec` — a single registered tool (name, callable, JSON schema).
* :class:`ToolRegistry` — maps tool names to callables, renders OpenAI-style
  schemas, and dispatches model tool calls to the underlying functions.

Keeping these here (rather than inside :mod:`loop.loop`) breaks the circular
import that arose when ``tools`` imported ``ToolRegistry`` from the loop while
the loop depended on ``tools`` to build the default registry. The registry has
no dependency on the loop, so both packages can import it freely.

A tool call inside an assistant message looks like::

    {"id": "call_1", "type": "function",
     "function": {"name": "read_file", "arguments": "{\\\"path\\\": \\\"x.py\\\"}"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from log.log import get_logger
from tools.tool_output_sanitizer import INJECTION_PATTERNS, sanitize_tool_output

logger = get_logger(__name__)


@dataclass
class ToolSpec:
    """A single registered tool: a name, a callable, and an optional JSON schema."""

    name: str
    func: Callable[..., Any]
    description: str = ""
    parameters: Optional[Dict[str, Any]] = None

    def to_openai_tool(self) -> Dict[str, Any]:
        """Render this tool as an OpenAI-compatible ``tools`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }


class ToolRegistry:
    """Maps tool names to callables and dispatches tool calls to them."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        *,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register (or replace) a tool by name."""
        if not name:
            raise ValueError("Tool name must be a non-empty string.")
        if not callable(func):
            raise ValueError(f"Tool '{name}' must be callable.")
        self._tools[name] = ToolSpec(name=name, func=func, description=description, parameters=parameters)
        logger.debug("Registered tool '%s'.", name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> List[str]:
        return list(self._tools.keys())

    def schemas(self) -> Optional[List[Dict[str, Any]]]:
        """Return OpenAI-style tool schemas, or ``None`` when no tools exist."""
        if not self._tools:
            return None
        return [spec.to_openai_tool() for spec in self._tools.values()]

    def get(self, name: str) -> Optional[ToolSpec]:
        """Return the ToolSpec for a tool name, or None if not found."""
        return self._tools.get(name)

    def dispatch(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a single tool call and return a ``tool`` message.

        Errors (unknown tool, bad JSON arguments, exceptions raised by the tool)
        are captured and returned as structured content so the model can recover
        instead of seeing a raw stack trace.
        """
        call_id, name, raw_args = parse_tool_call(tool_call)

        if name not in self._tools:
            logger.warning("Model requested unknown tool '%s'.", name)
            return tool_message(call_id, name, _error(f"Unknown tool: {name!r}"))

        try:
            args = parse_arguments(raw_args)
        except ValueError as exc:
            logger.warning("Invalid JSON arguments for tool '%s': %s", name, exc)
            return tool_message(call_id, name, _error(f"Invalid tool arguments: {exc}"))

        spec = self._tools[name]
        purpose = format_tool_purpose(name, args, spec.description)

        try:
            result = spec.func(**args)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
            logger.warning("Tool '%s' raised: %s", name, exc)
            return tool_message(call_id, name, _error(f"Tool execution failed: {exc}"))

        raw_output = _stringify(result)
        path = ""
        if isinstance(args, dict):
            p = args.get("path") or args.get("file") or args.get("file_path") or ""
            path = str(p) if p else ""
        sanitized_output = sanitize_tool_output(raw_output, path=path)
        return tool_message(call_id, name, sanitized_output)


# --------------------------------------------------------------------------- #
# Tool-call parsing / message helpers
# --------------------------------------------------------------------------- #
def parse_tool_call(tool_call: Dict[str, Any]) -> tuple[str, str, Any]:
    """Extract (id, name, raw_arguments) from an OpenAI-style tool call dict."""
    call_id = tool_call.get("id", "") or ""
    function = tool_call.get("function", {}) or {}
    name = function.get("name", "") or ""
    raw_args = function.get("arguments", {})
    return call_id, name, raw_args


def parse_arguments(raw_args: Any) -> Dict[str, Any]:
    """Parse tool-call arguments that may be a JSON string or already a dict."""
    if raw_args is None or raw_args == "":
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must decode to a JSON object.")
        return parsed
    raise ValueError(f"Unsupported argument type: {type(raw_args).__name__}")


def format_tool_purpose(name: str, args: Dict[str, Any], description: str = "") -> str:
    """
    Format the purpose of a tool call in plain language that is easy for anyone to understand.
    """
    if not isinstance(args, dict):
        args = {}

    path = args.get("path") or args.get("file") or args.get("file_path") or ""
    query = args.get("query") or args.get("search_term") or args.get("pattern") or ""
    command = args.get("command") or args.get("cmd") or ""
    url = args.get("url") or ""
    issue_key = args.get("issue_key") or args.get("ticket") or ""

    if name in ("read_file", "cat"):
        start = args.get("start_line")
        end = args.get("end_line")
        if start and end:
            return f"Reading file '{path}' (lines {start}-{end})"
        if path:
            return f"Reading file '{path}'"
        return "Reading file"

    if name in ("write_file", "create_file"):
        if path:
            return f"Writing file '{path}'"
        return "Writing file"

    if name in ("delete_file", "remove_file", "delete", "unlink"):
        if path:
            return f"Deleting '{path}'"
        return "Deleting file"

    if name == "edit_file":
        if path:
            return f"Editing file '{path}'"
        return "Editing file"

    if name == "apply_patch":
        if path:
            return f"Applying patch to '{path}'"
        return "Applying patch"

    if name == "list_dir":
        if path and path != ".":
            return f"Listing directory contents of '{path}'"
        return "Listing directory contents"

    if name in ("glob_search", "file_search"):
        pattern = args.get("pattern") or query
        if pattern:
            return f"Searching for files matching '{pattern}'"
        return "Searching for files"

    if name == "grep_search":
        if query:
            return f"Searching file contents for '{query}'"
        return "Searching file contents"

    if name == "codebase_search":
        if query:
            return f"Searching codebase for '{query}'"
        return "Searching codebase"

    if name in ("run_terminal", "run_command"):
        if command:
            return f"Executing terminal command '{command}'"
        return "Executing terminal command"

    if name == "run_tests":
        if path:
            return f"Running tests in '{path}'"
        return "Running test suite"

    if name == "lint":
        if path:
            return f"Linting '{path}'"
        return "Linting workspace"

    if name == "type_check":
        if path:
            return f"Type checking '{path}'"
        return "Type checking workspace"

    if name in ("get_errors", "diagnostics"):
        if path:
            return f"Checking syntax errors in '{path}'"
        return "Checking syntax errors in workspace"

    if name == "format_code":
        if path:
            return f"Formatting code in '{path}'"
        return "Formatting code in workspace"

    if name == "git_status":
        return "Checking git repository status"

    if name == "git_diff":
        if path:
            return f"Checking git diff for '{path}'"
        return "Checking git repository diff"

    if name == "git_commit":
        msg = args.get("message", "")
        if msg:
            return f"Creating git commit: '{msg}'"
        return "Creating git commit"

    if name == "read_excel":
        sheet = args.get("sheet")
        if path and sheet:
            return f"Reading sheet '{sheet}' in Excel file '{path}'"
        if path:
            return f"Reading Excel file '{path}'"
        return "Reading Excel file"

    if name == "write_excel":
        if path:
            return f"Writing Excel file '{path}'"
        return "Writing Excel file"

    if name in ("create_pptx", "create_powerpoint"):
        title = args.get("title")
        if path and title:
            return f"Creating PowerPoint presentation '{path}' with title '{title}'"
        if path:
            return f"Creating PowerPoint presentation '{path}'"
        return "Creating PowerPoint presentation"

    if name == "web_search":
        if query:
            return f"Searching web for '{query}'"
        return "Searching web"

    if name == "fetch_url":
        if url:
            return f"Fetching web page '{url}'"
        return "Fetching web page"

    if name in ("get_jira_ticket", "jira_ticket"):
        if issue_key:
            return f"Fetching Jira ticket '{issue_key}'"
        return "Fetching Jira ticket"

    if description:
        return description.strip()

    if args:
        rendered_args = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"Executing {name} ({rendered_args})"
    return f"Executing {name}"


def tool_message(call_id: str, name: str, content: str) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _stringify(result: Any) -> str:
    """Render a tool result as a string the model can read."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)


__all__ = [
    "ToolSpec",
    "ToolRegistry",
    "parse_tool_call",
    "parse_arguments",
    "format_tool_purpose",
    "tool_message",
    "INJECTION_PATTERNS",
    "sanitize_tool_output",
]

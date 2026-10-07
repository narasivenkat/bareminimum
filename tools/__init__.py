"""Tool package aggregator.

Each tool lives in its own sub-package under ``tools/`` and exposes a
``register(registry)`` function. ``build_default_registry`` imports every tool
module and registers it into a fresh :class:`ToolRegistry`, giving the agent
loop the full tool set in one call.
"""

from __future__ import annotations

from importlib import import_module
from typing import List

from tools.registry import ToolRegistry, ToolSpec
from tools.tool_output_sanitizer import INJECTION_PATTERNS, sanitize_tool_output

# Ordered list of tool sub-packages to load.
MODULES: List[str] = [
    "tools.read_file",
    "tools.write_file",
    "tools.delete_file",
    "tools.list_dir",
    "tools.file_search",
]


def build_default_registry() -> ToolRegistry:
    """Import all tool modules and register them into a new registry."""
    registry = ToolRegistry()
    for module_name in MODULES:
        module = import_module(module_name)
        register = getattr(module, "register", None)
        if register is None:
            raise RuntimeError(f"Tool module {module_name!r} has no register() function.")
        register(registry)
    return registry


__all__ = [
    "build_default_registry",
    "MODULES",
    "ToolRegistry",
    "ToolSpec",
    "INJECTION_PATTERNS",
    "sanitize_tool_output",
]

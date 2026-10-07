"""
Orchestrator & Sub-Agents Architecture implementation using AutoGen.

Exports:
- AutoGenOrchestratorSystem: Main multi-agent orchestrator system class.
- run_orchestrator: Async entry point for executing orchestrator tasks.
- orchestrate_read_tool_calls: Execute parallel LLM read tool calls via Orchestrator.
- Async parallel-safe read tools: async_read_file, async_list_dir, async_file_search.
- Async synchronized write tool: async_write_file.
- Concurrency primitives: get_write_lock, get_read_semaphore.
"""

from orchestrator.orchestrator import (
    AutoGenOrchestratorSystem,
    get_autogen_llm_config,
    orchestrate_read_tool_calls,
    run_orchestrator,
)
from orchestrator.tools import (
    async_file_search,
    async_list_dir,
    async_read_file,
    async_write_file,
    get_read_semaphore,
    get_write_lock,
)

__all__ = [
    "AutoGenOrchestratorSystem",
    "get_autogen_llm_config",
    "orchestrate_read_tool_calls",
    "run_orchestrator",
    "async_read_file",
    "async_list_dir",
    "async_file_search",
    "async_write_file",
    "get_write_lock",
    "get_read_semaphore",
]
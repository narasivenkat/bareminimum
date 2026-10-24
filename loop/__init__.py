"""Agent loop package."""

from loop.loop import (
    AgentResult,
    ToolRegistry,
    ToolSpec,
    run_agent,
    make_llm_model,
    prune_historical_tool_outputs,
    DEFAULT_SYSTEM_PROMPT,
)

__all__ = [
    "AgentResult",
    "ToolRegistry",
    "ToolSpec",
    "run_agent",
    "make_llm_model",
    "prune_historical_tool_outputs",
    "DEFAULT_SYSTEM_PROMPT",
]

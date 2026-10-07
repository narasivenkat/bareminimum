"""
Conversation context memory compaction module.

When the running conversation token count exceeds a threshold percentage of the model's
context window, older assistant and tool messages are summarized into a single note,
preserving the system prompt, user messages, and the most recent N messages verbatim.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from config.config import ConfigManager
from memory.token_counter import count_tokens

logger = logging.getLogger(__name__)

_DEFAULT_CONTEXT_WINDOW = 128000
_DEFAULT_THRESHOLD_PCT = 70.0
_DEFAULT_KEEP_RECENT = 6
_DEFAULT_TOKEN_ENCODING = "cl100k_base"


def get_memory_config() -> Dict[str, Any]:
    """Retrieve memory configuration options from ConfigManager with fallback defaults."""
    enabled = ConfigManager.get_raw("MEMORY", "ENABLED", True)
    if not isinstance(enabled, bool):
        enabled = str(enabled).strip().lower() in ("true", "1", "yes")

    context_window = ConfigManager.get_int("MEMORY", "CONTEXT_WINDOW", _DEFAULT_CONTEXT_WINDOW)
    if context_window is None or context_window <= 0:
        context_window = _DEFAULT_CONTEXT_WINDOW

    threshold_pct_raw = ConfigManager.get_raw("MEMORY", "THRESHOLD_PCT", _DEFAULT_THRESHOLD_PCT)
    try:
        threshold_pct = float(threshold_pct_raw) if threshold_pct_raw is not None else _DEFAULT_THRESHOLD_PCT
    except (TypeError, ValueError):
        threshold_pct = _DEFAULT_THRESHOLD_PCT

    keep_recent = ConfigManager.get_int("MEMORY", "KEEP_RECENT", _DEFAULT_KEEP_RECENT)
    if keep_recent is None or keep_recent < 1:
        keep_recent = _DEFAULT_KEEP_RECENT

    token_encoding = ConfigManager.get("MEMORY", "TOKEN_ENCODING", _DEFAULT_TOKEN_ENCODING) or _DEFAULT_TOKEN_ENCODING

    return {
        "enabled": enabled,
        "context_window": context_window,
        "threshold_pct": threshold_pct,
        "keep_recent": keep_recent,
        "token_encoding": token_encoding,
    }


def get_context_window(model_name: Optional[str] = None) -> int:
    """
    Resolve context window (in tokens) for a given model_name or fallback to configuration.

    Checks ``LLM.MODELS`` for a matching model entry, falling back to ``MEMORY.CONTEXT_WINDOW``.
    """
    if model_name:
        models = ConfigManager.get_raw("LLM", "MODELS", [])
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and entry.get("NAME") == model_name:
                    cw = entry.get("CONTEXT_WINDOW")
                    if cw is not None:
                        try:
                            cw_int = int(cw)
                            if cw_int > 0:
                                return cw_int
                        except (TypeError, ValueError):
                            pass

    cfg = get_memory_config()
    return cfg["context_window"]


def should_compact(
    messages: List[Dict[str, Any]],
    *,
    context_window: Optional[int] = None,
    threshold_pct: Optional[float] = None,
    encoding_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> bool:
    """
    Check whether context memory token count exceeds the threshold percentage of context window.
    """
    cfg = get_memory_config()
    if not cfg["enabled"]:
        return False

    window = context_window if context_window is not None else get_context_window(model_name)
    pct = threshold_pct if threshold_pct is not None else cfg["threshold_pct"]
    enc = encoding_name if encoding_name is not None else cfg["token_encoding"]

    current_tokens = count_tokens(messages, encoding_name=enc)
    threshold_tokens = (pct / 100.0) * window

    return current_tokens > threshold_tokens


def default_summarizer(to_summarize: List[Dict[str, Any]]) -> str:
    """Default plaintext summarizer for older assistant and tool messages."""
    summary_lines = []
    for msg in to_summarize:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls")

        if role == "assistant":
            if content:
                preview = content[:200] + "..." if len(content) > 200 else content
                summary_lines.append(f"Assistant response: {preview}")
            if tool_calls and isinstance(tool_calls, list):
                tool_names = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        if isinstance(fn, dict) and fn.get("name"):
                            tool_names.append(fn["name"])
                if tool_names:
                    summary_lines.append(f"Assistant called tools: {', '.join(tool_names)}")

        elif role == "tool":
            tool_name = msg.get("name") or "tool"
            preview = content[:200] + "..." if len(content) > 200 else content
            summary_lines.append(f"Tool [{tool_name}] output: {preview}")

        else:
            if content:
                preview = content[:200] + "..." if len(content) > 200 else content
                summary_lines.append(f"{role.capitalize()}: {preview}")

    return "\n".join(summary_lines) if summary_lines else "Previous conversation turns processed."


def compact_memory(
    messages: List[Dict[str, Any]],
    *,
    keep_recent: Optional[int] = None,
    custom_summarizer: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
    encoding_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Compact conversation history.

    Replaces older assistant and tool messages with a summary note while preserving:
    - System prompt and user messages
    - The most recent `keep_recent` messages
    """
    cfg = get_memory_config()
    recent_limit = keep_recent if keep_recent is not None else cfg["keep_recent"]

    if len(messages) <= recent_limit + 1:
        return list(messages)

    # Avoid starting recent window with an orphan tool message whose assistant call was in older
    split_idx = len(messages) - recent_limit
    while split_idx > 0 and messages[split_idx].get("role") == "tool":
        split_idx -= 1

    if split_idx <= 1:
        return list(messages)

    older = messages[:split_idx]
    recent = messages[split_idx:]

    system_msgs = [m for m in older if m.get("role") == "system"]
    user_msgs = [m for m in older if m.get("role") == "user"]
    to_summarize = [m for m in older if m.get("role") in ("assistant", "tool")]

    if not to_summarize:
        return list(messages)

    summarizer = custom_summarizer or default_summarizer
    summary_text = summarizer(to_summarize)

    summary_message = {
        "role": "user",
        "content": (
            "[Context Compaction Note: The following is a summary of previous turns "
            f"compacted to fit context memory limits]:\n{summary_text}"
        ),
    }

    compacted = system_msgs + user_msgs + [summary_message] + recent
    logger.info(
        "Memory compacted: %d messages -> %d messages (%d older messages summarized).",
        len(messages),
        len(compacted),
        len(to_summarize),
    )
    return compacted


__all__ = [
    "compact_memory",
    "default_summarizer",
    "get_context_window",
    "get_memory_config",
    "should_compact",
]

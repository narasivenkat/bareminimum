"""
Agent loop — the control spine of the harness.

The loop turns a stateless chat model into an agent by repeatedly:

    ask the model -> if it wants a tool, run the tool -> feed the result back -> repeat

It is deliberately *model-agnostic*: the model is supplied as a callable so the
loop can be unit-tested with a fake model and re-used with any provider. A hard
upper limit on the number of turns (read from ``AGENT.MAX_TURNS`` in
``resources/config.yaml`` unless overridden) guarantees the loop always
terminates.

Message shape (OpenAI-compatible ``dict``)::

    {"role": "system",    "content": "..."}
    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ ... ]}
    {"role": "tool",      "tool_call_id": "call_1", "name": "read_file", "content": "..."}

A tool call inside an assistant message::

    {"id": "call_1", "type": "function",
     "function": {"name": "read_file", "arguments": "{\"path\": \"x.py\"}"}}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from config.config import ConfigManager
from log.log import get_logger
from memory import (
    TokenTracker,
    compact_memory,
    count_tokens,
    should_compact,
)
from orchestrator import orchestrate_read_tool_calls
from tools.registry import (
    ToolRegistry,
    ToolSpec,
    format_tool_purpose,
    parse_arguments,
    parse_tool_call,
)

logger = get_logger(__name__)

# A model is any callable that takes the running message list plus the available
# tool schemas and returns a single assistant message dict.
ModelFn = Callable[[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], Dict[str, Any]]

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent. You analyse code, write code, and write tests. "
    "Use the available tools when you need to inspect or change the workspace. "
    "Whenever you call a tool, you MUST also write one short narration of plain "
    "text in the message content that narrates why you want to use the tool for (example: 'Reading loop.py to inspect the agent loop.'"
    "Do not emit a tool call with empty narration."
    "Make read only tool calls like list_dir, read_file, file_search in parallel for multiple directories or files rather than one by one."
    "When a specific file name is provided in then sharpen file search using the details instead of a broad search or directory listing"
    "Make write tool calls like write_file one by one only"
    "When writing to an existing file write only what is required on top of the existing content and do not overwrite the entire file unless explicitly asked to do so."
    "When the task is complete, reply with a final answer and no tool calls."
)

READ_TOOLS = {
    "read_file",
    "cat",
    "list_dir",
    "file_search",
    "search_files",
    "glob_search",
    "grep_search",
    "codebase_search",
    "read_excel",
    "fetch_url",
    "web_search",
}

# Fallback used only if the config value is missing or invalid.
_FALLBACK_MAX_TURNS = 10

# Stop reasons returned in AgentResult.stop_reason.
STOP_FINAL_ANSWER = "final_answer"
STOP_MAX_TURNS = "max_turns"


@dataclass
class AgentResult:
    """The outcome of an agent run."""

    final_text: str
    messages: List[Dict[str, Any]]
    turns: int
    stop_reason: str
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


def resolve_max_turns(max_turns: Optional[int]) -> int:
    """Resolve the turn limit from the explicit argument or config, and validate it."""
    if max_turns is None:
        max_turns = ConfigManager.get_int("AGENT", "MAX_TURNS", _FALLBACK_MAX_TURNS)
    if max_turns is None or max_turns < 1:
        logger.warning("Invalid MAX_TURNS %r; falling back to %d.", max_turns, _FALLBACK_MAX_TURNS)
        return _FALLBACK_MAX_TURNS
    return int(max_turns)


def run_agent(
    user_text: str,
    *,
    model: ModelFn,
    tools: Optional[ToolRegistry] = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_turns: Optional[int] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    model_name: Optional[str] = None,
) -> AgentResult:
    """
    Run the agent loop until the model returns a final answer or the turn limit
    is reached.

    Args:
        user_text: The user's request. Ignored when ``messages`` is supplied.
        model: Callable ``(messages, tool_schemas) -> assistant message dict``.
        tools: Optional :class:`ToolRegistry`. When ``None`` the agent runs
            without tools (text-only).
        system_prompt: System prompt used to seed a fresh conversation.
        max_turns: Hard upper limit on model turns. Falls back to
            ``AGENT.MAX_TURNS`` in config when ``None``.
        messages: Optional pre-built message list to continue an existing
            conversation instead of starting from ``system`` + ``user``.
        model_name: Optional name of the model behind ``model``. When given, the
            compaction context window is taken from that model's
            ``CONTEXT_WINDOW`` in config instead of the fixed ``MEMORY`` value.

    Returns:
        An :class:`AgentResult` describing the final answer, full transcript,
        number of turns taken, and why the loop stopped.
    """
    if not callable(model):
        raise ValueError("`model` must be a callable.")

    registry = tools if tools is not None else ToolRegistry()
    limit = resolve_max_turns(max_turns)

    if messages is not None:
        conversation: List[Dict[str, Any]] = list(messages)
    else:
        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
    logger.info("\nContinuing existing conversation with %d messages.", len(conversation))

    tool_call_count = 0
    turns = 0
    last_content = ""
    compaction_count = 0
    token_tracker = TokenTracker()

    while turns < limit:
        turns += 1

        # Check and compact context memory if tokens exceed threshold
        if should_compact(conversation, model_name=model_name):
            conversation = compact_memory(conversation)
            compaction_count += 1
            logger.info(
                "Context memory exceeded threshold; compacted conversation history (compaction #%d).",
                compaction_count,
            )

        schemas = registry.schemas() if tools is not None else None
        turn_input_data = [conversation]
        if schemas:
            turn_input_data.append(schemas)
        token_tracker.add_input(turn_input_data)

        assistant = model(conversation, registry.schemas())
        assistant = _normalise_assistant(assistant)
        token_tracker.add_output(assistant)

        conversation.append(assistant)
        last_content = assistant.get("content") or last_content

        tool_calls = assistant.get("tool_calls")
        logger.info("Agent turn %d/%d: %s", turns, limit, _describe_action(assistant, tool_calls, registry))
        if not tool_calls:
            logger.debug("Agent finished with a final answer on turn %d.", turns)
            total_cost = token_tracker.calculate_cost(model_name=model_name)
            return AgentResult(
                final_text=assistant.get("content") or "",
                messages=conversation,
                turns=turns,
                stop_reason=STOP_FINAL_ANSWER,
                tool_calls=tool_call_count,
                input_tokens=token_tracker.input_tokens,
                output_tokens=token_tracker.output_tokens,
                total_cost=total_cost,
                metadata={
                    "compactions": compaction_count,
                    "input_tokens": token_tracker.input_tokens,
                    "output_tokens": token_tracker.output_tokens,
                    "total_tokens": token_tracker.total_tokens,
                    "total_cost": total_cost,
                },
            )

        read_calls = []
        read_indices = []

        for idx, call in enumerate(tool_calls):
            tool_call_count += 1
            _, name, _ = parse_tool_call(call)
            if name in READ_TOOLS:
                read_calls.append(call)
                read_indices.append(idx)

        if read_calls:
            read_results = orchestrate_read_tool_calls(read_calls, registry)
            read_results_map = {read_indices[i]: read_results[i] for i in range(len(read_calls))}
        else:
            read_results_map = {}

        for idx, call in enumerate(tool_calls):
            if idx in read_results_map:
                conversation.append(read_results_map[idx])
            else:
                conversation.append(registry.dispatch(call))

    # Turn limit reached while the model still wanted to call tools. Make one
    # final call with tools disabled and an explicit instruction so the model
    # is forced to produce a text answer instead of leaving the user with an
    # empty response.
    logger.warning("Agent hit the turn limit (%d); forcing a final answer.", limit)
    final_text = last_content
    try:
        forcing_msg = {
            "role": "user",
            "content": (
                "You have reached the tool-use limit and may not call any "
                "more tools. Using everything you have gathered so far, "
                "write your best final answer now as plain text."
            ),
        }
        forcing_conversation = conversation + [forcing_msg]
        token_tracker.add_input(forcing_conversation)
        forced = _normalise_assistant(model(forcing_conversation, None))
        token_tracker.add_output(forced)
        conversation.append(forced)
        final_text = forced.get("content") or last_content
    except Exception as exc:  # noqa: BLE001 - fall back to last known content
        logger.warning("Forced final answer failed: %s", exc)

    total_cost = token_tracker.calculate_cost(model_name=model_name)
    return AgentResult(
        final_text=final_text,
        messages=conversation,
        turns=turns,
        stop_reason=STOP_MAX_TURNS,
        tool_calls=tool_call_count,
        input_tokens=token_tracker.input_tokens,
        output_tokens=token_tracker.output_tokens,
        total_cost=total_cost,
        metadata={
            "compactions": compaction_count,
            "input_tokens": token_tracker.input_tokens,
            "output_tokens": token_tracker.output_tokens,
            "total_tokens": token_tracker.total_tokens,
            "total_cost": total_cost,
        },
    )


def make_llm_model(model_name: str) -> ModelFn:
    """
    Build a tool-capable model adapter bound to a specific model name.

    The agent loop calls a model as ``model(messages, tool_schemas)``, so it
    cannot pass a model name itself. This factory captures ``model_name`` in a
    closure and produces a :data:`ModelFn` that routes every request to that
    model while still forwarding tool schemas and preserving ``tool_calls``.

    Args:
        model_name: The model to call (e.g. ``"gemini-3.6-flash"``).

    Returns:
        A :data:`ModelFn` suitable for :func:`run_agent`'s ``model`` argument.
    """

    def model(
        messages: List[Dict[str, Any]],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        from llm.llm_client import call_llm

        return call_llm(
            messages,
            tools=list(tools) if tools else None,
            model_name=model_name,
        )

    return model


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _describe_action(
    assistant: Dict[str, Any],
    tool_calls: Optional[List[Dict[str, Any]]],
    registry: Optional[ToolRegistry] = None,
) -> str:
    """
    Build a short human-readable description of what the agent is doing this turn.

    When the model narrates its action (per the system prompt) the assistant
    message carries ``content`` alongside its ``tool_calls``; that narration is
    shown alongside a plain-language description of the tool call's purpose.
    If the model called tools without narrating, fall back to describing the tool call
    and its purpose in easy-to-understand plain language. For a final-answer turn
    it reports the reply length and a short preview.
    """
    if tool_calls:
        narration = (assistant.get("content") or "").strip().replace("\n", " ")
        tool_desc = _describe_tool_calls(tool_calls, registry)
        if narration:
            return f"{narration} [{tool_desc}]"
        return tool_desc

    content = assistant.get("content") or ""
    text = content.strip().replace("\n", " ")
    if not text:
        return "producing a final answer (empty)"
    preview = text if len(text) <= 80 else text[:77] + "..."
    return f'final answer ({len(text)} chars): "{preview}"'


def _describe_tool_calls(
    tool_calls: List[Dict[str, Any]],
    registry: Optional[ToolRegistry] = None,
) -> str:
    """Render tool calls with their tool name and human-readable purpose for logging."""
    parts = []
    for call in tool_calls:
        _, name, raw_args = parse_tool_call(call)
        try:
            args = parse_arguments(raw_args)
        except ValueError:
            args = {}
        desc = ""
        if registry:
            spec = registry.get(name)
            if spec:
                desc = spec.description
        purpose = format_tool_purpose(name, args, desc)
        parts.append(f"{name or '?'}: {purpose}")
    verb = "calling tool" if len(parts) == 1 else "calling tools"
    return f"{verb} {'; '.join(parts)}"


def _normalise_assistant(assistant: Any) -> Dict[str, Any]:
    """Coerce a model return value into a well-formed assistant message dict."""
    if not isinstance(assistant, dict):
        raise TypeError(
            f"Model must return a message dict, got {type(assistant).__name__}."
        )
    msg = dict(assistant)
    msg.setdefault("role", "assistant")
    if "content" not in msg:
        msg["content"] = None
    return msg

"""
Unit tests for the memory package (token counting, memory compaction, cost computation, and agent loop wiring).
"""

from unittest.mock import patch

from config.config import ConfigManager
from memory.compaction import (
    compact_memory,
    default_summarizer,
    get_context_window,
    get_memory_config,
    should_compact,
)
from memory.token_counter import (
    TokenTracker,
    compute_cost,
    count_message_tokens,
    count_string_tokens,
    count_tokens,
    get_model_pricing,
)
from loop.loop import AgentResult, STOP_FINAL_ANSWER, run_agent
from tools.registry import ToolRegistry


def test_token_counter_basic():
    assert count_string_tokens("") == 0
    assert count_string_tokens("hello world") > 0

    msg = {"role": "user", "content": "Hello, world!"}
    assert count_message_tokens(msg) > 0

    tool_call_msg = {
        "role": "assistant",
        "content": "Running tool",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'},
            }
        ],
    }
    assert count_message_tokens(tool_call_msg) > count_message_tokens(msg)
    assert count_tokens([msg, tool_call_msg]) == count_message_tokens(msg) + count_message_tokens(tool_call_msg)


def test_token_tracker():
    tracker = TokenTracker()
    assert tracker.input_tokens == 0
    assert tracker.output_tokens == 0
    assert tracker.total_tokens == 0

    tracker.add_input("Prompt message")
    assert tracker.input_tokens > 0
    assert tracker.total_tokens == tracker.input_tokens

    tracker.add_output("Completion response")
    assert tracker.output_tokens > 0
    assert tracker.total_tokens == tracker.input_tokens + tracker.output_tokens

    # Direct integer additions
    tracker.add_input(100)
    tracker.add_output(50)
    assert tracker.total_tokens == tracker.input_tokens + tracker.output_tokens

    tracker.reset()
    assert tracker.input_tokens == 0
    assert tracker.output_tokens == 0
    assert tracker.total_tokens == 0


def test_compute_cost():
    # 1,000,000 input tokens at $0.15/1M + 1,000,000 output tokens at $0.60/1M = $0.75
    cost = compute_cost(1_000_000, 1_000_000, input_cost_per_1m=0.15, output_cost_per_1m=0.60)
    assert abs(cost - 0.75) < 1e-6

    # Model specific lookup tests
    claude_cost = compute_cost(1_000_000, 1_000_000, model_name="claude-opus-4.8")
    assert abs(claude_cost - 90.0) < 1e-6  # 15 + 75 = 90

    gemini_cost = compute_cost(1_000_000, 1_000_000, model_name="gemini-3.6-flash")
    assert abs(gemini_cost - 0.75) < 1e-6  # 0.15 + 0.60 = 0.75

    # TokenTracker cost helper methods
    tracker = TokenTracker(input_tokens=500_000, output_tokens=500_000)
    assert abs(tracker.calculate_cost(input_cost_per_1m=1.0, output_cost_per_1m=2.0) - 1.50) < 1e-6
    assert tracker.total_cost > 0.0


def test_get_model_pricing():
    ConfigManager.reset()
    in_c, out_c = get_model_pricing("gemini-3.6-flash")
    assert in_c == 0.15
    assert out_c == 0.60

    in_c2, out_c2 = get_model_pricing("claude-opus-4.8")
    assert in_c2 == 15.00
    assert out_c2 == 75.00

    # Unknown model falls back to LLM config default
    in_def, out_def = get_model_pricing("unknown-model-123")
    assert in_def == 0.15
    assert out_def == 0.60


def test_get_model_pricing_custom_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
LLM:
  INPUT_COST_PER_1M: 0.25
  OUTPUT_COST_PER_1M: 1.00
  MODELS:
    - NAME: custom-model
      INPUT_COST_PER_1M: 2.50
      OUTPUT_COST_PER_1M: 5.00
""",
        encoding="utf-8",
    )

    ConfigManager.set_config_file_path(str(config_file))
    in_c, out_c = get_model_pricing("custom-model")
    assert in_c == 2.50
    assert out_c == 5.00

    in_def, out_def = get_model_pricing("unknown-model")
    assert in_def == 0.25
    assert out_def == 1.00

    ConfigManager.reset()


def test_get_context_window():
    ConfigManager.reset()
    # gemini-3.6-flash has CONTEXT_WINDOW: 1048576 in config.yaml
    assert get_context_window("gemini-3.6-flash") == 1048576
    # claude-opus-4.8 has CONTEXT_WINDOW: 200000
    assert get_context_window("claude-opus-4.8") == 200000
    # Unknown model falls back to config default (128000)
    assert get_context_window("unknown-model-xyz") == 128000
    assert get_context_window(None) == 128000


def test_should_compact():
    small_messages = [{"role": "user", "content": "hi"}]
    assert not should_compact(small_messages, context_window=1000, threshold_pct=70)

    # Generate a large message sequence
    large_content = "Word " * 500  # ~500 tokens
    large_messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": large_content},
    ]
    # Threshold = 70% of 500 = 350 tokens. large_messages > 350 tokens.
    assert should_compact(large_messages, context_window=500, threshold_pct=70)


def test_compact_memory_preserves_structure():
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Initial request"},
        {"role": "assistant", "content": "First response", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "File content 1"},
        {"role": "assistant", "content": "Second response", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_file", "content": "File content 2"},
        {"role": "assistant", "content": "Third response"},
        {"role": "user", "content": "Followup question"},
        {"role": "assistant", "content": "Fourth response"},
        {"role": "user", "content": "Recent question"},
    ]

    compacted = compact_memory(messages, keep_recent=3)

    # Verify length is reduced
    assert len(compacted) < len(messages)

    # Verify system prompt is at index 0
    assert compacted[0]["role"] == "system"
    assert compacted[0]["content"] == "System prompt"

    # Verify user initial request is preserved
    assert compacted[1]["role"] == "user"
    assert compacted[1]["content"] == "Initial request"

    # Verify summary note message exists
    summary_msgs = [m for m in compacted if "[Context Compaction Note" in m.get("content", "")]
    assert len(summary_msgs) == 1

    # Verify recent messages are preserved at the end
    assert compacted[-3:] == messages[-3:]


def test_agent_loop_token_tracking_and_compaction():
    ConfigManager.reset()
    registry = ToolRegistry()

    def dummy_read(path: str) -> str:
        return "Line " * 100

    registry.register("read_file", dummy_read, description="Reads file")

    turn_count = 0

    def mock_model(messages, tools):
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            return {
                "role": "assistant",
                "content": "I will read the file now",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
                    }
                ],
            }
        return {"role": "assistant", "content": "Final task complete."}

    # Run agent with low context window so compaction triggers
    with patch("memory.compaction.get_context_window", return_value=100):
        res = run_agent("Perform heavy task", model=mock_model, tools=registry, max_turns=5, model_name="gemini-3.6-flash")

    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.total_cost > 0.0
    assert res.metadata["input_tokens"] == res.input_tokens
    assert res.metadata["output_tokens"] == res.output_tokens
    assert res.metadata["total_tokens"] == res.input_tokens + res.output_tokens
    assert res.metadata["total_cost"] == res.total_cost
    assert res.metadata["compactions"] >= 1

"""
Unit tests for the memory package (token counting, memory compaction, cost computation, and agent loop wiring).
"""

from unittest.mock import patch
import pytest

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


def test_token_tracker_telemetry_side_by_side():
    tracker = TokenTracker(model_name="gemini-3.6-flash")

    # Simulate turn 1 with API usage response attached to assistant message
    turn1_msg = {
        "role": "assistant",
        "content": "Hello world",
        "_api_usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    }
    tracker.add_input("User input prompt for turn 1")
    tracker.add_output(turn1_msg)

    assert tracker.input_tokens > 0
    assert tracker.output_tokens > 0
    assert tracker.api_input_tokens == 120
    assert tracker.api_output_tokens == 40
    assert tracker.api_total_tokens == 160

    # Test comparison report
    report = tracker.get_comparison_report("gemini-3.6-flash")
    assert "tiktoken" in report
    assert "api_response" in report
    assert "difference" in report

    assert report["api_response"]["input_tokens"] == 120
    assert report["api_response"]["output_tokens"] == 40
    assert report["api_response"]["total_tokens"] == 160
    assert report["api_response"]["cost"] == pytest.approx(tracker.calculate_cost(source="api"))

    # Turn history check
    assert len(tracker.turn_history) == 1
    t1 = tracker.turn_history[0]
    assert t1["api_response"]["input"] == 120
    assert t1["api_response"]["output"] == 40


def test_compute_cost():
    # 1,000,000 input tokens at $0.15/1M + 1,000,000 output tokens at $0.60/1M = $0.75
    cost = compute_cost(1_000_000, 1_000_000, input_cost_per_1m=0.15, output_cost_per_1m=0.60)
    assert abs(cost - 0.75) < 1e-6

    # Model specific lookup tests
    claude_cost = compute_cost(1_000_000, 1_000_000, model_name="claude-opus-4.8")
    assert abs(claude_cost - 90.0) < 1e-6  # 15 + 75 = 90

    gemini_cost = compute_cost(1_000_000, 1_000_000, model_name="gemini-3.6-flash")
    assert abs(gemini_cost - 0.75) < 1e-6  # 0.15 + 0.60 = 0.75


def test_get_memory_config_and_context_window():
    ConfigManager.reset()
    cfg = get_memory_config()
    assert cfg["enabled"] is True
    assert cfg["context_window"] == 128000
    assert cfg["threshold_pct"] == 50
    assert cfg["keep_recent"] == 6

    cw_default = get_context_window()
    assert cw_default == 128000

    cw_gemini = get_context_window("gemini-3.6-flash")
    assert cw_gemini == 1048576

    cw_claude = get_context_window("claude-opus-4.8")
    assert cw_claude == 200000


def test_should_compact():
    ConfigManager.reset()

    # Small conversation should not trigger compaction
    small_messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    assert should_compact(small_messages, context_window=100000) is False

    # Force should_compact = True by using artificially tiny context_window
    assert should_compact(small_messages, context_window=10) is True


def test_default_summarizer():
    messages = [
        {"role": "user", "content": "Write a script to list files."},
        {"role": "assistant", "content": "Running list_dir tool..."},
        {"role": "tool", "content": "file1.py\nfile2.py\nfile3.py"},
    ]
    summary = default_summarizer(messages)
    assert isinstance(summary, str)
    assert "user: Write a script to list files." in summary
    assert "tool: file1.py" in summary


def test_compact_memory():
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Initial request"},
        {"role": "assistant", "content": "Turn 1 thought"},
        {"role": "tool", "content": "Tool 1 output"},
        {"role": "assistant", "content": "Turn 2 thought"},
        {"role": "tool", "content": "Tool 2 output"},
        {"role": "assistant", "content": "Turn 3 thought"},
        {"role": "tool", "content": "Tool 3 output"},
        {"role": "user", "content": "Follow up request"},
        {"role": "assistant", "content": "Recent response"},
    ]

    compacted = compact_memory(messages, keep_recent=3)

    assert len(compacted) < len(messages)
    # Verify System prompt and first user message are preserved
    assert compacted[0]["content"] == "System prompt"
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
                "_api_usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
            }
        return {
            "role": "assistant",
            "content": "Final task complete.",
            "_api_usage": {"prompt_tokens": 220, "completion_tokens": 15, "total_tokens": 235},
        }

    # Run agent with low context window so compaction triggers
    with patch("memory.compaction.get_context_window", return_value=100):
        res = run_agent("Perform heavy task", model=mock_model, tools=registry, max_turns=5, model_name="gemini-3.6-flash")

    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.total_cost > 0.0
    assert res.api_input_tokens == 370
    assert res.api_output_tokens == 45
    assert res.api_total_cost > 0.0
    assert "tiktoken" in res.token_usage_comparison
    assert "api_response" in res.token_usage_comparison
    assert res.token_usage_comparison["api_response"]["input_tokens"] == 370
    assert res.metadata["input_tokens"] == res.input_tokens
    assert res.metadata["api_input_tokens"] == 370

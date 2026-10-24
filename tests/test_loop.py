"""Unit tests for loop/loop.py."""

from unittest.mock import patch
from loop.loop import (
    AgentResult,
    STOP_FINAL_ANSWER,
    STOP_MAX_TURNS,
    prune_historical_tool_outputs,
    run_agent,
)
from tools.registry import ToolRegistry


def test_run_agent_simple_final_answer():
    def mock_model(messages, tools):
        return {"role": "assistant", "content": "Done!"}

    res = run_agent("hello", model=mock_model)
    assert res.final_text == "Done!"
    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.turns == 1
    assert res.elapsed_time >= 0.0
    assert res.metadata["elapsed_time"] == res.elapsed_time


def test_run_agent_tool_interaction():
    registry = ToolRegistry()

    def add_tool(a: int, b: int) -> int:
        return a + b

    registry.register("add", add_tool, description="Adds two numbers")

    call_count = 0

    def mock_model(messages, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "role": "assistant",
                "content": "Calculating sum",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                    }
                ],
            }
        return {"role": "assistant", "content": "The answer is 5."}

    res = run_agent("What is 2+3?", model=mock_model, tools=registry)
    assert res.final_text == "The answer is 5."
    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.turns == 2
    assert res.tool_calls == 1
    assert res.elapsed_time >= 0.0


def test_run_agent_parallel_read_tool_calls():
    registry = ToolRegistry()

    def read_file(path: str) -> str:
        return f"content of {path}"

    def list_dir(path: str) -> list:
        return ["fileA.txt", "fileB.txt"]

    registry.register("read_file", read_file, description="Reads a file")
    registry.register("list_dir", list_dir, description="Lists directory")

    call_count = 0

    def mock_model(messages, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "role": "assistant",
                "content": "Reading files and listing dir in parallel",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "file1.txt"}'},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "file2.txt"}'},
                    },
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                    },
                ],
            }
        return {"role": "assistant", "content": "Analyzed all files and directories."}

    with patch("loop.loop.orchestrate_read_tool_calls", wraps=None) as mock_orch:
        from orchestrator import orchestrate_read_tool_calls
        mock_orch.side_effect = orchestrate_read_tool_calls

        res = run_agent("Check workspace", model=mock_model, tools=registry)
        assert res.final_text == "Analyzed all files and directories."
        assert res.tool_calls == 3
        mock_orch.assert_called_once()
        # Verify 3 tool response messages were added to conversation
        tool_msgs = [m for m in res.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 3
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        assert tool_msgs[1]["tool_call_id"] == "call_2"
        assert tool_msgs[2]["tool_call_id"] == "call_3"
        assert res.elapsed_time >= 0.0


def test_run_agent_max_turns():
    def mock_model(messages, tools):
        return {
            "role": "assistant",
            "content": "Thinking...",
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "unknown_tool", "arguments": "{}"},
                }
            ],
        }

    res = run_agent("Loop forever", model=mock_model, max_turns=2)
    assert res.turns == 2
    assert res.stop_reason == STOP_MAX_TURNS
    assert res.elapsed_time >= 0.0
    assert res.metadata["elapsed_time"] == res.elapsed_time


def test_prune_historical_tool_outputs():
    long_content = "X" * 500
    short_content = "Small tool output"

    conversation = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Turn 1 request"},
        {"role": "assistant", "content": "Turn 1 call", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": long_content},
        {"role": "assistant", "content": "Turn 2 call", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "list_dir", "content": short_content},
        {"role": "assistant", "content": "Turn 3 call", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c3", "name": "read_file", "content": long_content},
        {"role": "assistant", "content": "Turn 4 response"},
        {"role": "user", "content": "Turn 5 request"},
        {"role": "assistant", "content": "Turn 5 call", "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c4", "name": "read_file", "content": long_content},
    ]

    pruned = prune_historical_tool_outputs(conversation, keep_recent_turns=2, content_threshold=300)

    assert len(pruned) == len(conversation)
    assert "[Tool 'read_file' output consumed in prior turn; content truncated to save tokens]" in pruned[3]["content"]
    assert pruned[5]["content"] == short_content
    assert pruned[7]["content"] == long_content
    assert pruned[11]["content"] == long_content


def test_run_agent_historical_tool_output_pruning():
    registry = ToolRegistry()
    large_output = "Line " * 100  # 500 chars

    def read_file(path: str) -> str:
        return large_output

    registry.register("read_file", read_file, description="Reads a file")

    turn_count = 0

    def mock_model(messages, tools):
        nonlocal turn_count
        turn_count += 1
        if turn_count < 5:
            return {
                "role": "assistant",
                "content": f"Turn {turn_count} reading",
                "tool_calls": [
                    {
                        "id": f"call_{turn_count}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "file.txt"}'},
                    }
                ],
            }
        return {"role": "assistant", "content": "All turns finished."}

    res = run_agent("Read file repeatedly", model=mock_model, tools=registry, max_turns=6)
    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.turns == 5

    # Check conversation history in result: older tool outputs should be truncated
    tool_msgs = [m for m in res.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 4

    # Earliest tool messages (turn 1) should be pruned
    assert "content truncated to save tokens" in tool_msgs[0]["content"]

    # Most recent tool message should still contain the full large output
    assert tool_msgs[-1]["content"] == large_output


def test_run_agent_api_telemetry_extraction_and_accounting():
    def mock_model_with_usage(messages, tools):
        return {
            "role": "assistant",
            "content": "Completed task with usage telemetry.",
            "_api_usage": {
                "prompt_tokens": 140,
                "completion_tokens": 35,
                "total_tokens": 175,
            },
        }

    res = run_agent("Do something", model=mock_model_with_usage, model_name="gemini-3.6-flash")
    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.api_input_tokens == 140
    assert res.api_output_tokens == 35
    assert res.api_total_cost > 0.0
    assert "tiktoken" in res.token_usage_comparison
    assert "api_response" in res.token_usage_comparison
    assert res.token_usage_comparison["api_response"]["input_tokens"] == 140
    assert res.token_usage_comparison["api_response"]["output_tokens"] == 35
    assert res.token_usage_comparison["api_response"]["total_tokens"] == 175

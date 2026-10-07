"""Unit tests for loop/loop.py."""

from unittest.mock import patch
from loop.loop import AgentResult, STOP_FINAL_ANSWER, STOP_MAX_TURNS, run_agent
from tools.registry import ToolRegistry


def test_run_agent_simple_final_answer():
    def mock_model(messages, tools):
        return {"role": "assistant", "content": "Done!"}

    res = run_agent("hello", model=mock_model)
    assert res.final_text == "Done!"
    assert res.stop_reason == STOP_FINAL_ANSWER
    assert res.turns == 1


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
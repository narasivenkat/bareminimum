"""Unit and integration tests for orchestrator/ package."""

import asyncio
import json
import logging
from io import StringIO
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.tools import (
    async_delete_file,
    async_file_search,
    async_list_dir,
    async_read_file,
    async_write_file,
    get_read_semaphore,
    get_write_lock,
)
from orchestrator.orchestrator import (
    AutoGenOrchestratorSystem,
    get_autogen_llm_config,
    orchestrate_read_tool_calls,
    run_orchestrator,
)
from tools._common import resolve_path
from tools.registry import ToolRegistry
from log.log import get_audit_logger, ConsoleFilter


@pytest.mark.asyncio
async def test_async_read_file_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    test_file = tmp_path / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3\nline 4\n", encoding="utf-8")

    res_str = await async_read_file("test.txt", start_line=1, end_line=2)
    data = json.loads(res_str)

    assert data["path"] == "test.txt"
    assert data["content"] == "line 1\nline 2"
    assert data["start_line"] == 1
    assert data["end_line"] == 2


@pytest.mark.asyncio
async def test_async_read_file_error_handled():
    res_str = await async_read_file("non_existent_file_xyz.txt")
    data = json.loads(res_str)

    assert "error" in data
    assert data["filepath"] == "non_existent_file_xyz.txt"


@pytest.mark.asyncio
async def test_async_list_dir_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "file1.txt").write_text("a", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    res_str = await async_list_dir(".")
    data = json.loads(res_str)

    assert data["path"] == "."
    assert "file1.txt" in data["entries"]
    assert "subdir/" in data["entries"]


@pytest.mark.asyncio
async def test_async_file_search_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "utils.py").write_text("x = 1", encoding="utf-8")

    res_str = await async_file_search("*.py")
    data = json.loads(res_str)

    assert "main.py" in data["files"]
    assert "utils.py" in data["files"]


@pytest.mark.asyncio
async def test_async_write_file_lock_serialization(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    execution_order = []
    lock = get_write_lock()

    async def slow_write(path: str, content: str):
        res_str = await async_write_file(path, content)
        execution_order.append(path)
        return json.loads(res_str)

    # Run multiple write operations concurrently
    tasks = [
        slow_write("file1.txt", "content 1"),
        slow_write("file2.txt", "content 2"),
        slow_write("file3.txt", "content 3"),
    ]

    results = await asyncio.gather(*tasks)

    for r in results:
        assert r["status"] == "success"

    assert (tmp_path / "file1.txt").read_text(encoding="utf-8") == "content 1"
    assert (tmp_path / "file2.txt").read_text(encoding="utf-8") == "content 2"
    assert (tmp_path / "file3.txt").read_text(encoding="utf-8") == "content 3"
    assert len(execution_order) == 3


@pytest.mark.asyncio
async def test_async_write_file_interactive_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    def mock_eof_input(prompt=""):
        raise EOFError("No interactive input")

    monkeypatch.setattr("builtins.input", mock_eof_input)

    res_str = await async_write_file("test_hitl.txt", "content", interactive=True)
    data = json.loads(res_str)

    assert data["status"] == "error"
    assert "User rejected write operation" in data["error"]
    assert not (tmp_path / "test_hitl.txt").exists()


@pytest.mark.asyncio
async def test_async_delete_file_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    file_to_del = tmp_path / "del.txt"
    file_to_del.write_text("delete me", encoding="utf-8")

    res_str = await async_delete_file("del.txt", interactive=False)
    data = json.loads(res_str)

    assert data["status"] == "success"
    assert data["result"]["deleted"] is True
    assert not file_to_del.exists()


@pytest.mark.asyncio
async def test_async_delete_file_error_handled(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    res_str = await async_delete_file("missing.txt", interactive=False)
    data = json.loads(res_str)

    assert data["status"] == "error"
    assert "filepath" in data
    assert "Path not found" in data["error"]


@pytest.mark.asyncio
async def test_parallel_read_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    for i in range(5):
        (tmp_path / f"file_{i}.txt").write_text(f"content {i}", encoding="utf-8")

    read_tasks = [async_read_file(f"file_{i}.txt") for i in range(5)]
    results = await asyncio.gather(*read_tasks)

    assert len(results) == 5
    for i, res_str in enumerate(results):
        data = json.loads(res_str)
        assert data["content"] == f"content {i}"


def test_get_autogen_llm_config():
    config = get_autogen_llm_config(model_name="test-model", temperature=0.5)
    assert config["temperature"] == 0.5
    assert len(config["config_list"]) == 1
    assert config["config_list"][0]["model"] == "test-model"


def test_orchestrate_read_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "f1.txt").write_text("hello 1", encoding="utf-8")
    (tmp_path / "f2.txt").write_text("hello 2", encoding="utf-8")

    registry = ToolRegistry()
    from tools.read_file import read_file
    from tools.list_dir import list_dir

    registry.register("read_file", read_file)
    registry.register("list_dir", list_dir)

    calls = [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
        {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f2.txt"}'}},
        {"id": "c3", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "."}'}},
    ]

    results = orchestrate_read_tool_calls(calls, registry)
    assert len(results) == 3
    assert results[0]["tool_call_id"] == "c1"
    assert "hello 1" in results[0]["content"]
    assert results[1]["tool_call_id"] == "c2"
    assert "hello 2" in results[1]["content"]
    assert results[2]["tool_call_id"] == "c3"
    assert "f1.txt" in results[2]["content"]


def test_orchestrate_read_tool_calls_logging(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "f1.txt").write_text("hello 1", encoding="utf-8")
    (tmp_path / "f2.txt").write_text("hello 2", encoding="utf-8")

    registry = ToolRegistry()
    from tools.read_file import read_file

    registry.register("read_file", read_file)

    single_call = [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
    ]

    multiple_calls = [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
        {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f2.txt"}'}},
    ]

    # Test 1 tool call
    with caplog.at_level(logging.INFO):
        caplog.clear()
        orchestrate_read_tool_calls(single_call, registry)
        assert "Executing 1 tool call..." in caplog.text
        assert "in parallel" not in caplog.text

    # Test multiple tool calls (> 1)
    with caplog.at_level(logging.INFO):
        caplog.clear()
        orchestrate_read_tool_calls(multiple_calls, registry)
        assert "Executing 2 tool calls in parallel..." in caplog.text


def test_orchestrate_read_tool_calls_console_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "f1.txt").write_text("hello 1", encoding="utf-8")
    (tmp_path / "f2.txt").write_text("hello 2", encoding="utf-8")

    registry = ToolRegistry()
    from tools.read_file import read_file

    registry.register("read_file", read_file)

    multiple_calls = [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
        {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "f2.txt"}'}},
    ]

    target_log_file = tmp_path / "app.log"
    logger = get_audit_logger("test_orchestration_filter_logger", log_file=str(target_log_file))

    stream = StringIO()
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.stream = stream

    orchestrate_read_tool_calls(multiple_calls, registry)

    # Output on console stream should NOT contain parallel execution message
    console_output = stream.getvalue()
    assert "Executing 2 tool calls in parallel" not in console_output

    # File log SHOULD contain the execution message
    log_content = target_log_file.read_text(encoding="utf-8")
    assert "Executing 2 tool calls in parallel" in log_content


@patch("orchestrator.orchestrator.autogen")
def test_autogen_orchestrator_init(mock_autogen):
    mock_autogen.UserProxyAgent = MagicMock()
    mock_autogen.AssistantAgent = MagicMock()

    system = AutoGenOrchestratorSystem(
        llm_config={"config_list": [{"model": "dummy"}]},
        human_input_mode="NEVER",
    )

    assert system.orchestrator is not None
    assert system.explorer_agent is not None
    assert system.writer_agent is not None


@pytest.mark.asyncio
@patch("orchestrator.orchestrator.autogen")
async def test_autogen_orchestrator_execute_task(mock_autogen):
    mock_orchestrator_agent = MagicMock()
    mock_orchestrator_agent.a_initiate_chat = AsyncMock(return_value={"summary": "ok"})
    mock_autogen.UserProxyAgent.return_value = mock_orchestrator_agent
    mock_autogen.AssistantAgent = MagicMock()

    system = AutoGenOrchestratorSystem(
        llm_config={"config_list": [{"model": "dummy"}]},
    )

    results = await system.execute_task(
        read_prompts=["Read task 1", "Read task 2"],
        write_prompts=["Write task 1"],
    )

    assert len(results["read_results"]) == 2
    assert len(results["write_results"]) == 1
    assert mock_orchestrator_agent.a_initiate_chat.call_count == 3


@pytest.mark.asyncio
@patch("orchestrator.orchestrator.autogen")
async def test_subagent_spinoff_logging(mock_autogen, caplog):
    mock_orchestrator_agent = MagicMock()
    mock_orchestrator_agent.a_initiate_chat = AsyncMock(return_value={"summary": "ok"})
    mock_autogen.UserProxyAgent.return_value = mock_orchestrator_agent
    
    explorer = MagicMock()
    explorer.name = "ExplorerSubAgent"
    writer = MagicMock()
    writer.name = "WriterAgent"
    mock_autogen.AssistantAgent.side_effect = [explorer, writer]

    system = AutoGenOrchestratorSystem(
        llm_config={"config_list": [{"model": "dummy"}]},
    )

    with caplog.at_level(logging.INFO):
        await system.execute_task(
            read_prompts=["Inspect main.py"],
            write_prompts=["Refactor main.py"],
        )

    logs = caplog.text
    assert "[Subagent Spun Off]" in logs
    assert "ExplorerSubAgent" in logs
    assert "Inspect main.py" in logs
    assert "WriterAgent" in logs
    assert "Refactor main.py" in logs


@pytest.mark.asyncio
@patch("orchestrator.orchestrator.AutoGenOrchestratorSystem")
async def test_run_orchestrator_wrapper(mock_system_cls):
    mock_instance = MagicMock()
    mock_instance.execute_task = AsyncMock(return_value={"read_results": [], "write_results": []})
    mock_system_cls.return_value = mock_instance

    res = await run_orchestrator(read_tasks=["task1"], write_tasks=["task2"])
    assert res == {"read_results": [], "write_results": []}
    mock_instance.execute_task.assert_called_once_with(read_prompts=["task1"], write_prompts=["task2"])

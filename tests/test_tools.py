"""Unit tests for tools package."""

import pytest
from pathlib import Path

from tools import build_default_registry
from tools._common import resolve_path
from tools.delete_file import delete_file
from tools.file_search import file_search
from tools.list_dir import list_dir
from tools.read_file import read_file
from tools.registry import ToolRegistry
from tools.write_file import write_file


def test_resolve_path_safety():
    # Attempting to resolve a path outside workspace should raise ValueError
    with pytest.raises(ValueError, match="escapes workspace root"):
        resolve_path("../../../../../../../etc/passwd")


def test_read_write_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    # Write file
    w_res = write_file(path="test.txt", content="hello world\nline two", overwrite=True, interactive=False)
    assert w_res["bytes_written"] > 0
    assert "diff" in w_res
    assert w_res["status"] == "approved_and_written"

    # Read file
    r_res = read_file(path="test.txt")
    assert r_res["content"] == "hello world\nline two"
    assert r_res["total_lines"] == 2

    # Read line range
    r_sub = read_file(path="test.txt", start_line=1, end_line=1)
    assert r_sub["content"] == "hello world"


def test_write_file_hitl_approval_granted(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    w_res = write_file(path="hitl_app.txt", content="content 1\ncontent 2", interactive=True)
    assert w_res["status"] == "approved_and_written"
    assert (tmp_path / "hitl_app.txt").read_text() == "content 1\ncontent 2"
    assert "diff" in w_res


def test_write_file_hitl_approval_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    with pytest.raises(PermissionError, match="User rejected write operation"):
        write_file(path="hitl_rej.txt", content="should not be written", interactive=True)

    assert not (tmp_path / "hitl_rej.txt").exists()


def test_write_file_hitl_approval_eof_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    def mock_eof_input(prompt=""):
        raise EOFError("Non-interactive terminal")

    monkeypatch.setattr("builtins.input", mock_eof_input)

    with pytest.raises(PermissionError, match="User rejected write operation"):
        write_file(path="hitl_eof.txt", content="should not be written", interactive=True)

    assert not (tmp_path / "hitl_eof.txt").exists()


def test_write_file_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "existing.txt").write_text("old line\n")

    w_res = write_file(path="existing.txt", content="new line\n", dry_run=True, interactive=True)
    assert w_res["status"] == "dry_run_simulated"
    assert w_res["bytes_written"] == 0
    assert "-old line" in w_res["diff"]
    assert "+new line" in w_res["diff"]
    # Verify file on disk was not modified
    assert (tmp_path / "existing.txt").read_text() == "old line\n"


def test_delete_file_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    file_to_del = tmp_path / "to_delete.txt"
    file_to_del.write_text("goodbye")

    res = delete_file(path="to_delete.txt", interactive=False)
    assert res["status"] == "approved_and_deleted"
    assert res["deleted"] is True
    assert not file_to_del.exists()


def test_delete_file_hitl_approval_granted(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    file_to_del = tmp_path / "del_app.txt"
    file_to_del.write_text("content")

    res = delete_file(path="del_app.txt", interactive=True)
    assert res["status"] == "approved_and_deleted"
    assert res["deleted"] is True
    assert not file_to_del.exists()


def test_delete_file_hitl_approval_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    file_to_del = tmp_path / "del_rej.txt"
    file_to_del.write_text("content")

    with pytest.raises(PermissionError, match="User rejected delete operation"):
        delete_file(path="del_rej.txt", interactive=True)

    assert file_to_del.exists()


def test_delete_file_hitl_approval_eof_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    def mock_eof_input(prompt=""):
        raise EOFError("Non-interactive terminal")

    monkeypatch.setattr("builtins.input", mock_eof_input)
    file_to_del = tmp_path / "del_eof.txt"
    file_to_del.write_text("content")

    with pytest.raises(PermissionError, match="User rejected delete operation"):
        delete_file(path="del_eof.txt", interactive=True)

    assert file_to_del.exists()


def test_delete_file_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    file_to_del = tmp_path / "dry_del.txt"
    file_to_del.write_text("preserve me")

    res = delete_file(path="dry_del.txt", dry_run=True, interactive=True)
    assert res["status"] == "dry_run_simulated"
    assert res["deleted"] is False
    assert file_to_del.exists()


def test_delete_directory_without_recursive_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    dir_to_del = tmp_path / "mydir"
    dir_to_del.mkdir()

    with pytest.raises(ValueError, match="is a directory. Set recursive=True"):
        delete_file(path="mydir", recursive=False, interactive=False)

    assert dir_to_del.exists()


def test_delete_directory_recursive_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    dir_to_del = tmp_path / "mydir"
    dir_to_del.mkdir()
    (dir_to_del / "nested.txt").write_text("nested")

    res = delete_file(path="mydir", recursive=True, interactive=False)
    assert res["status"] == "approved_and_deleted"
    assert res["deleted"] is True
    assert not dir_to_del.exists()


def test_delete_nonexistent_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    with pytest.raises(FileNotFoundError):
        delete_file(path="missing.txt", interactive=False)


def test_list_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "file1.txt").write_text("a")

    res = list_dir(path=".")
    assert "entries" in res
    assert "subfolder/" in res["entries"]
    assert "file1.txt" in res["entries"]


def test_file_search(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path))

    # Setup directory structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "src" / "utils.py").write_text("def util(): pass")
    (tmp_path / "src" / "data.json").write_text("{}")

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_main(): assert True")

    # Ignored directory
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "ignored.py").write_text("# ignored")

    # Search for all python files
    res = file_search(pattern="*.py")
    assert res["count"] == 3
    assert "src/main.py" in res["files"]
    assert "src/utils.py" in res["files"]
    assert "tests/test_main.py" in res["files"]
    assert ".venv/lib/ignored.py" not in res["files"]

    # Search for test files specifically
    res_test = file_search(pattern="test_*.py")
    assert res_test["count"] == 1
    assert res_test["files"] == ["tests/test_main.py"]

    # Search within subfolder
    res_sub = file_search(pattern="*.py", path="src")
    assert res_sub["count"] == 2
    assert "src/main.py" in res_sub["files"]
    assert "src/utils.py" in res_sub["files"]

    # Search for json files
    res_json = file_search(pattern="*.json")
    assert res_json["count"] == 1
    assert res_json["files"] == ["src/data.json"]

    # Search with no matches
    res_empty = file_search(pattern="*.nonexistent")
    assert res_empty["count"] == 0
    assert res_empty["files"] == []


def test_build_default_registry_includes_delete_file():
    registry = build_default_registry()
    assert "delete_file" in registry.names
    assert "remove_file" in registry.names
    assert "delete" in registry.names
    assert "unlink" in registry.names
    assert "file_search" in registry.names


def test_tool_registry():
    registry = ToolRegistry()

    def dummy_tool(val: str) -> str:
        return f"echo: {val}"

    registry.register("dummy", dummy_tool, description="Dummy tool")
    assert "dummy" in registry.names

    tool_call = {
        "id": "call_123",
        "function": {
            "name": "dummy",
            "arguments": '{"val": "hello"}',
        },
    }

    result = registry.dispatch(tool_call)
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_123"
    assert "echo: hello" in result["content"]

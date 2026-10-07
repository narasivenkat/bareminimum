"""Unit tests for run.py CLI functionality."""

import os
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import pytest

import run
from loop.loop import AgentResult, STOP_FINAL_ANSWER


def test_handle_sigint():
    with pytest.raises(SystemExit) as exc_info:
        run.handle_sigint()
    assert exc_info.value.code == 0


@patch("run.available_models")
def test_change_model_dialog_select_valid(mock_avail, monkeypatch, capsys):
    mock_avail.return_value = ["model-alpha", "model-beta"]
    run.active_model = "model-alpha"

    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    selected = run.change_model_dialog()

    assert selected == "model-beta"
    assert run.active_model == "model-beta"
    captured = capsys.readouterr().out
    assert "Active model changed to: model-beta" in captured


@patch("run.available_models")
def test_change_model_dialog_keep_current(mock_avail, monkeypatch, capsys):
    mock_avail.return_value = ["model-alpha", "model-beta"]
    run.active_model = "model-alpha"

    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    selected = run.change_model_dialog()

    assert selected == "model-alpha"
    assert run.active_model == "model-alpha"
    captured = capsys.readouterr().out
    assert "Keeping active model: model-alpha" in captured


@patch("run.available_models")
def test_change_model_dialog_invalid(mock_avail, monkeypatch, capsys):
    mock_avail.return_value = ["model-alpha", "model-beta"]
    run.active_model = "model-alpha"

    monkeypatch.setattr("builtins.input", lambda prompt="": "invalid")
    selected = run.change_model_dialog()

    assert selected == "model-alpha"
    assert run.active_model == "model-alpha"
    captured = capsys.readouterr().out
    assert "Keeping active model: model-alpha" in captured


@patch("run.subprocess.run")
def test_run_bash_mode_command_and_exit(mock_subproc, monkeypatch, capsys):
    inputs = iter(["echo hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    run.run_bash_mode()

    mock_subproc.assert_called_once_with("echo hello", shell=True)
    captured = capsys.readouterr().out
    assert "Entering bash mode." in captured
    assert "Exiting bash mode." in captured


def test_run_bash_mode_cd(tmp_path, monkeypatch, capsys):
    target = tmp_path / "bash_dir"
    target.mkdir()

    inputs = iter([f"cd {target}", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    run.run_bash_mode()

    assert os.getcwd() == str(target.resolve())
    assert os.environ["HARNESS_WORKSPACE"] == str(target.resolve())
    captured = capsys.readouterr().out
    assert "Exiting bash mode." in captured


def test_run_bash_mode_cd_invalid(tmp_path, monkeypatch, capsys):
    target = tmp_path / "non_existent_bash_dir"

    inputs = iter([f"cd {target}", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    run.run_bash_mode()

    captured = capsys.readouterr().out
    assert "bash: cd:" in captured
    assert "No such file or directory" in captured


@patch("run.run_agent")
@patch("run.build_default_registry")
def test_start_cli_task_execution(mock_registry, mock_run_agent, monkeypatch, capsys):
    run.active_model = "test-model"
    mock_run_agent.return_value = AgentResult(
        final_text="Task finished successfully",
        messages=[],
        turns=1,
        stop_reason=STOP_FINAL_ANSWER,
    )

    inputs = iter(["Test task input", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    mock_run_agent.assert_called_once()
    assert mock_run_agent.call_args[0][0] == "Test task input"
    captured = capsys.readouterr().out
    assert "Task finished successfully" in captured


@patch("run.change_model_dialog")
def test_start_cli_model_command(mock_change_dialog, monkeypatch):
    inputs = iter(["/model", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    mock_change_dialog.assert_called_once()


@patch("run.run_bash_mode")
def test_start_cli_bash_command(mock_bash_mode, monkeypatch):
    inputs = iter(["/bash", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    mock_bash_mode.assert_called_once()


def test_start_cli_orchestrator_command(monkeypatch, capsys):
    inputs = iter(["/orchestrator", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    captured = capsys.readouterr().out
    assert "Orchestrator is active" in captured


@patch("run.run_orchestrator")
def test_run_orchestrator_cli(mock_orchestrate, capsys):
    mock_orchestrate.return_value = {"read_results": [], "write_results": []}
    res = run.run_orchestrator_cli(["read task 1"], ["write task 1"])
    assert res == {"read_results": [], "write_results": []}
    captured = capsys.readouterr().out
    assert "Running AutoGen Orchestrator" in captured
    assert "Execution finished successfully" in captured


@patch("run.run_agent")
@patch("run.build_default_registry")
def test_main_with_args(mock_registry, mock_run_agent, monkeypatch, capsys):
    run.active_model = "test-model"
    mock_run_agent.return_value = AgentResult(
        final_text="Direct task output",
        messages=[],
        turns=1,
        stop_reason=STOP_FINAL_ANSWER,
    )

    monkeypatch.setattr(sys, "argv", ["run.py", "Perform direct task"])
    run.main()

    mock_run_agent.assert_called_once()
    assert mock_run_agent.call_args[0][0] == "Perform direct task"
    captured = capsys.readouterr().out
    assert "Direct task output" in captured


@patch("run.run_bash_mode")
@patch("run.start_cli")
def test_main_with_bash_arg(mock_start_cli, mock_bash_mode, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "/bash"])
    run.main()

    mock_bash_mode.assert_called_once()
    mock_start_cli.assert_called_once()

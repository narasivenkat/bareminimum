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
        input_tokens=100,
        output_tokens=50,
        total_cost=0.000123,
        elapsed_time=1.45,
    )

    inputs = iter(["Test task input", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, BaseException):
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
    assert "[Tokens: 100 in, 50 out (150 total) | Cost: $0.000123 | Time: 1.45s]" in captured


@patch("run.change_model_dialog")
def test_start_cli_model_command(mock_change_dialog, monkeypatch):
    inputs = iter(["/model", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, BaseException):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    mock_change_dialog.assert_called_once()


@patch("run.run_agent")
@patch("run.build_default_registry")
def test_main_with_task_arg(mock_registry, mock_run_agent, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run.py", "Do", "something"])
    mock_run_agent.return_value = AgentResult(
        final_text="Direct task completed",
        messages=[],
        turns=1,
        stop_reason=STOP_FINAL_ANSWER,
        input_tokens=10,
        output_tokens=20,
        total_cost=0.00005,
        elapsed_time=0.5,
    )

    run.main()

    mock_run_agent.assert_called_once()
    assert mock_run_agent.call_args[0][0] == "Do something"
    captured = capsys.readouterr().out
    assert "Direct task completed" in captured


@patch("run.change_model_dialog")
@patch("run.start_cli")
def test_main_with_model_arg(mock_start_cli, mock_change_dialog, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "/model"])
    run.main()

    mock_change_dialog.assert_called_once()
    mock_start_cli.assert_called_once()


@patch("run.run_bash_mode")
@patch("run.start_cli")
def test_main_with_bash_arg(mock_start_cli, mock_bash_mode, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "/bash"])
    run.main()

    mock_bash_mode.assert_called_once()
    mock_start_cli.assert_called_once()


@patch("graph.dbconn.Neo4jConnection")
def test_handle_ingest_command(mock_conn_cls):
    mock_conn = mock_conn_cls.return_value
    mock_conn.verify_connectivity.return_value = True

    with patch("run.ingester.ingest_context") as mock_ingest_context:
        run.handle_ingest_command()

    mock_conn.verify_connectivity.assert_called_once()
    mock_ingest_context.assert_called_once_with(db_conn=mock_conn)


@patch("run.handle_ingest_command")
def test_start_cli_ingest_command(mock_handle_ingest, monkeypatch):
    inputs = iter(["/ingest", KeyboardInterrupt()])

    def mock_input(prompt=""):
        res = next(inputs)
        if isinstance(res, BaseException):
            raise res
        return res

    monkeypatch.setattr("builtins.input", mock_input)

    with pytest.raises(SystemExit) as exc_info:
        run.start_cli()

    assert exc_info.value.code == 0
    mock_handle_ingest.assert_called_once()


def test_print_result_summary_api_only(capsys):
    comp = {
        "tiktoken": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150, "cost": 0.0001},
        "api_response": {"input_tokens": 110, "output_tokens": 52, "total_tokens": 162, "cost": 0.00011},
        "difference": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "cost": 0.00001},
    }
    res = AgentResult(
        final_text="Done",
        messages=[],
        turns=1,
        stop_reason="STOP",
        input_tokens=100,
        output_tokens=50,
        total_cost=0.0001,
        token_usage_comparison=comp,
        elapsed_time=1.5,
    )

    run._print_result_summary(res)

    captured = capsys.readouterr().out
    assert "Token Usage Telemetry" in captured
    assert "API Response:" in captured
    assert "Tiktoken:" not in captured
    assert "Difference:" not in captured
    assert "[Tokens: 110 in, 52 out (162 total) | Cost: $0.000110 | Time: 1.50s]" in captured

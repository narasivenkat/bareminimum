"""Unit tests for llm/llm_client.py."""

from unittest.mock import MagicMock, patch

from llm.llm_client import (
    LLMClient,
    _get_model_params,
    available_models,
    default_model,
)


@patch("llm.llm_client._get_models")
def test_default_model(mock_get_models):
    mock_get_models.return_value = [
        {"NAME": "model-1", "DEFAULT": False},
        {"NAME": "model-2", "DEFAULT": True},
    ]

    assert default_model() == "model-2"


@patch("llm.llm_client._get_models")
def test_available_models(mock_get_models):
    mock_get_models.return_value = [
        {"NAME": "model-1", "DEFAULT": False},
        {"NAME": "model-2", "DEFAULT": True},
    ]

    models = available_models()
    assert models[0] == "model-2"
    assert "model-1" in models


@patch("llm.llm_client._get_models")
def test_get_model_params(mock_get_models):
    mock_get_models.return_value = [
        {"NAME": "model-1", "TEMPERATURE": 0.7, "MAX_TOKENS": 2048, "TOP_P": 0.9},
    ]

    params = _get_model_params("model-1")
    assert params["model_name"] == "model-1"
    assert params["temperature"] == 0.7
    assert params["max_tokens"] == 2048
    assert params["top_p"] == 0.9


# --------------------------------------------------------------------------- #
# LLMClient Integration Tests
# --------------------------------------------------------------------------- #

@patch("llm.llm_client._get_access_token")
@patch("llm.llm_client.OpenAI")
def test_llm_client_complete_success(mock_openai, mock_token):
    mock_token.return_value = "fake-token"
    mock_message = MagicMock(content="Hello world", tool_calls=None)
    mock_choice = MagicMock(message=mock_message)
    mock_completion = MagicMock(choices=[mock_choice])

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion
    mock_openai.return_value = mock_client

    client = LLMClient()

    res = client.complete([{"role": "user", "content": "Hi"}])
    assert res.content == "Hello world"


@patch("time.sleep")
@patch("llm.llm_client._get_access_token")
@patch("llm.llm_client.OpenAI")
def test_llm_client_call_llm_structure(mock_openai, mock_token, mock_sleep):
    mock_token.return_value = "fake-token"
    mock_tc = MagicMock()
    mock_tc.id = "call_123"
    mock_tc.function.name = "read_file"
    mock_tc.function.arguments = '{"path": "a.txt"}'

    mock_message = MagicMock(content="Executing tool", tool_calls=[mock_tc])
    mock_choice = MagicMock(message=mock_message)
    mock_completion = MagicMock(choices=[mock_choice])

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion
    mock_openai.return_value = mock_client

    client = LLMClient()

    res = client.call_llm([{"role": "user", "content": "Inspect file"}])

    assert res["role"] == "assistant"
    assert res["content"] == "Executing tool"
    assert len(res["tool_calls"]) == 1
    assert res["tool_calls"][0]["id"] == "call_123"
    assert res["tool_calls"][0]["function"]["name"] == "read_file"

"""Unit tests for auth/oauth.py."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from auth.oauth import (
    _is_token_expired,
    cache_oauth_token,
    get_oauth_token,
    get_oauth_token_from_cache,
    get_oauth_token_retrieval_timestamp_from_cache,
    retrieve_oauth_token,
)


@patch("auth.oauth.requests.post")
@patch("auth.oauth.ConfigManager.get")
def test_get_oauth_token_failure(mock_cfg_get, mock_post):
    mock_cfg_get.return_value = "dummy"
    mock_post.side_effect = requests.exceptions.RequestException("Connection error")

    token_data = get_oauth_token()
    assert token_data is None


@patch("auth.oauth.requests.post")
def test_get_oauth_token_from_env_vars(mock_post, monkeypatch):
    monkeypatch.setenv("LLM_OAUTH_TOKEN_URL", "https://example.com/oauth/token")
    monkeypatch.setenv("LLM_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("LLM_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LLM_OAUTH_SCOPE", "test-scope")
    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("HTTP_PROXY_URL", "http://proxy.example.com:8080")

    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "token123", "expires_in": 3600}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    token_data = get_oauth_token()

    assert token_data == {"access_token": "token123", "expires_in": 3600}
    mock_post.assert_called_once_with(
        "https://example.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "scope": "test-scope",
        },
        proxies={
            "http": "http://proxy.example.com:8080",
            "https": "http://proxy.example.com:8080",
        },
    )

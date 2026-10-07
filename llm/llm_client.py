"""
LLM client adapter for the exception claims validation pipeline.

Authenticates via OAuth bearer token (auth/oauth.py) and calls the
LLM API endpoint using the OpenAI-compatible client. Configuration
is read from resources/config.yaml via config/config.py.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from openai import OpenAI

from config.config import ConfigManager
from auth.oauth import retrieve_oauth_token

logger = logging.getLogger(__name__)


def _get_client_params() -> dict[str, Any]:
    """Read generic, model-independent client settings from config.yaml."""
    cfg = ConfigManager
    return {
        "base_url": cfg.get("LLM", "BASE_API_URL"),
        "api_url": cfg.get("LLM", "API_URL"),
        "max_retries": int(cfg.get("LLM", "RETRY.MAX_RETRIES", "3") or "3"),
        "initial_backoff": float(cfg.get("LLM", "RETRY.INITIAL_BACKOFF", "0.5") or "0.5"),
        "max_backoff": float(cfg.get("LLM", "RETRY.MAX_BACKOFF", "8.0") or "8.0"),
        "backoff_multiplier": float(cfg.get("LLM", "RETRY.BACKOFF_MULTIPLIER", "2") or "2"),
    }


def _get_models() -> list[dict[str, Any]]:
    """Return the raw per-model config entries from ``LLM.MODELS``."""
    models = ConfigManager.get_raw("LLM", "MODELS", [])
    if not isinstance(models, list):
        logger.warning("LLM.MODELS is not a list; ignoring.")
        return []
    return [m for m in models if isinstance(m, dict) and m.get("NAME")]


def _get_model_params(model_name: str | None = None) -> dict[str, Any]:
    """
    Resolve generation parameters for a specific model.

    Looks up the matching entry in ``LLM.MODELS`` by ``NAME``. Falls back to the
    default model when ``model_name`` is omitted, and to sane hard-coded values
    for any field a model entry does not specify.
    """
    resolved = model_name or default_model()
    entry: dict[str, Any] = {}
    for candidate in _get_models():
        if candidate.get("NAME") == resolved:
            entry = candidate
            break
    else:
        logger.warning(
            "Model %r not found in LLM.MODELS; using default generation parameters.",
            resolved,
        )

    def _num(key: str, default: float) -> float:
        value = entry.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning("LLM.MODELS[%s].%s is not numeric: %r", resolved, key, value)
            return default

    return {
        "model_name": resolved,
        "temperature": _num("TEMPERATURE", 0.1),
        "max_tokens": int(_num("MAX_TOKENS", 4096)),
        "top_p": _num("TOP_P", 0.2),
    }


def _get_access_token() -> str:
    """Obtain a valid OAuth bearer token, raising on failure."""
    token_data = retrieve_oauth_token()
    if token_data is None or token_data.get("access_token") is None:
        raise RuntimeError(
            "Failed to obtain OAuth access token. "
            "Check OAuth config in resources/config.yaml."
        )
    return token_data["access_token"]


def default_model() -> str:
    """
    Return the default model name.

    Uses the first ``LLM.MODELS`` entry flagged ``DEFAULT: true``; if none is
    flagged, falls back to the first configured model, and finally to a
    hard-coded default when no models are configured at all.
    """
    models = _get_models()
    for entry in models:
        if entry.get("DEFAULT") is True:
            return str(entry["NAME"])
    if models:
        return str(models[0]["NAME"])
    return "gemini-3.6-flash"


def available_models() -> list[str]:
    """
    Return the list of selectable model names from ``LLM.MODELS`` (their NAMEs).

    The default model (the entry flagged ``DEFAULT: true``) is always placed
    first, so it appears at the top of the selection menu.
    """
    names = [m["NAME"] for m in _get_models()]
    default = default_model()
    ordered = [default] + [n for n in names if n != default]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for name in ordered:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


class LLMClient:
    """
    LLM Client adapter with built-in exponential backoff retries.
    """

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict]] = None,
        response_format: Optional[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_name: str | None = None,
    ) -> Any:
        """
        Send a chat-completion request and return the raw assistant message object.

        Handles OAuth authentication and exponential backoff retries.

        Raises:
            RuntimeError: If authentication fails.
            Exception: If all retry attempts are exhausted.
        """
        client_params = _get_client_params()
        model_params = _get_model_params(model_name)
        access_token = _get_access_token()

        _temperature = temperature if temperature is not None else model_params["temperature"]
        _max_tokens = max_tokens if max_tokens is not None else model_params["max_tokens"]
        _model_name = model_params["model_name"]

        client = OpenAI(
            api_key=access_token,
            base_url=client_params["base_url"],
        )

        max_retries = client_params["max_retries"]
        backoff = client_params["initial_backoff"]
        last_exception: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    "[LLM] Attempt %d/%d — model=%s, messages=%d, tools=%d",
                    attempt,
                    max_retries,
                    _model_name,
                    len(messages),
                    len(tools) if tools else 0,
                )

                kwargs: dict[str, Any] = {
                    "model": _model_name,
                    "messages": messages,
                    "max_tokens": _max_tokens,
                    "temperature": _temperature,
                    "top_p": model_params["top_p"],
                }
                if tools:
                    kwargs["tools"] = tools
                if response_format is not None:
                    kwargs["response_format"] = response_format

                completion = client.chat.completions.create(**kwargs)
                return completion.choices[0].message

            except Exception as e:
                last_exception = e
                logger.warning(
                    "[LLM] Attempt %d failed (Error): %s. Retrying in %.1fs…",
                    attempt,
                    e,
                    backoff,
                )
                time.sleep(min(backoff, client_params["max_backoff"]))
                backoff *= client_params["backoff_multiplier"]

        raise last_exception  # type: ignore[misc]

    def call_llm(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a chat-completion request and return a full OpenAI-style assistant dict.
        """
        message = self.complete(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model_name=model_name,
        )

        assistant: dict[str, Any] = {"role": "assistant", "content": message.content}

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]

        logger.debug(
            "[LLM] Response received (%d chars, %d tool call(s))",
            len(message.content or ""),
            len(tool_calls) if tool_calls else 0,
        )
        return assistant


def _complete(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict]] = None,
    response_format: Optional[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_name: str | None = None,
) -> Any:
    """Module-level wrapper around LLMClient.complete()."""
    client = LLMClient()
    return client.complete(
        messages,
        tools=tools,
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
        model_name=model_name,
    )


def call_llm(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict]] = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Module-level wrapper around LLMClient.call_llm()."""
    client = LLMClient()
    return client.call_llm(
        messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        model_name=model_name,
    )

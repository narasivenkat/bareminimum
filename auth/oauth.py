"""OAuth token retrieval and caching module."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

import requests

from config.config import ConfigManager
from constants.constants import TOKEN_EXPIRY_BUFFER
from log.log import get_logger

logger = get_logger(__name__)

# Persistent thread-local token cache. A single module-level instance keeps the
# token alive per thread.
_token_cache = threading.local()
# Serialises token refresh so concurrent callers don't fetch simultaneously.
_token_lock = threading.Lock()


def get_oauth_token() -> Optional[Dict[str, Any]]:
    """Fetch a new OAuth token using client credentials grant.

    Checks environment variables first, falling back to configuration values.

    Returns:
        Token dictionary response containing 'access_token' or None on failure.
    """
    token_url = (
        os.getenv("LLM_OAUTH_TOKEN_URL")
        or os.getenv("OAUTH_TOKEN_URL")
        or ConfigManager.get("OAuth", "OAUTH_TOKEN_URL")
    )
    client_id = (
        os.getenv("LLM_OAUTH_CLIENT_ID")
        or os.getenv("OAUTH_CLIENT_ID")
        or ConfigManager.get("OAuth", "CLIENT_ID")
    )
    client_secret = (
        os.getenv("LLM_OAUTH_CLIENT_SECRET")
        or os.getenv("OAUTH_CLIENT_SECRET")
        or ConfigManager.get("OAuth", "CLIENT_SECRET")
    )
    scope = (
        os.getenv("LLM_OAUTH_SCOPE")
        or os.getenv("OAUTH_SCOPE")
        or ConfigManager.get("OAuth", "SCOPE")
    )
    use_proxy = os.getenv("USE_PROXY") or ConfigManager.get("Proxy", "USE_PROXY")
    http_proxy = (
        os.getenv("HTTP_PROXY_URL")
        or os.getenv("HTTP_PROXY")
        or ConfigManager.get("Proxy", "HTTP_PROXY")
    )

    is_proxy_enabled = str(use_proxy).lower() in ("true", "1", "yes")
    if is_proxy_enabled:
        proxies = {
            "http": http_proxy,
            "https": http_proxy,
        }
    else:
        proxies = {
            "http": None,
            "https": None,
        }
    proxies = {k: v for k, v in proxies.items() if v is not None}

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope
    }

    try:
        logger.info("Obtaining OAuth token")
        response = requests.post(token_url, headers=headers, data=data, proxies=proxies)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if access_token:
            logger.info("Access token successfully obtained")
            return token_data
        else:
            logger.error("Error: 'access_token' not found in response: %s", token_data)
            return None
    except requests.exceptions.RequestException as e:
        logger.error("Error obtaining OAuth token: %s", e)
        return None


def cache_oauth_token(oauth_token: Dict[str, Any]) -> None:
    """Store an OAuth token dict and retrieval timestamp in thread-local storage."""
    _token_cache.oauth_token = oauth_token
    _token_cache.oauth_token_retrieval_timestamp = int(time.time())


def get_oauth_token_from_cache() -> Optional[Dict[str, Any]]:
    """Retrieve the cached OAuth token for the current thread."""
    return getattr(_token_cache, "oauth_token", None)


def get_oauth_token_retrieval_timestamp_from_cache() -> Optional[int]:
    """Retrieve the timestamp when the cached token was fetched."""
    timestamp = getattr(_token_cache, "oauth_token_retrieval_timestamp", None)
    return int(timestamp) if timestamp is not None else None


def _is_token_expired(oauth_token: Dict[str, Any]) -> bool:
    """Return True when there is no cached timestamp or the token is at/near expiry."""
    cached_timestamp = get_oauth_token_retrieval_timestamp_from_cache()
    if cached_timestamp is None:
        return True
    try:
        expires_in = int(oauth_token.get("expires_in", 0))
    except (TypeError, ValueError):
        return True
    expiry_time = cached_timestamp + expires_in - TOKEN_EXPIRY_BUFFER
    return expiry_time <= int(time.time())


def retrieve_oauth_token() -> Optional[Dict[str, Any]]:
    """Return a valid OAuth token, reusing the cached one until it nears expiry."""
    with _token_lock:
        oauth_token = get_oauth_token_from_cache()

        needs_refresh = (
            oauth_token is None
            or oauth_token.get("access_token") is None
            or _is_token_expired(oauth_token)
        )

        if needs_refresh:
            oauth_token = get_oauth_token()
            if oauth_token is None or oauth_token.get("access_token") is None:
                logger.error(
                    "Error: unable to obtain OAuth access token; "
                    "check OAuth/Proxy config and credentials."
                )
                return None
            cache_oauth_token(oauth_token)

        return oauth_token


__all__ = [
    "get_oauth_token",
    "cache_oauth_token",
    "get_oauth_token_from_cache",
    "get_oauth_token_retrieval_timestamp_from_cache",
    "retrieve_oauth_token",
]

"""Structured JSON audit logger with automated PII and credential masking, file logging, and user-friendly terminal output."""

import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Masking regex for secrets, bearer tokens, and emails
SENSITIVE_REGEXES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r'("?(?:client_secret|api_key|password)"?\s*:\s*")([^"]+)(")', re.IGNORECASE), r"\1[REDACTED_SECRET]\3"),
    (re.compile(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE), r"[REDACTED_EMAIL]"),
]

LOG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = os.path.join(LOG_DIR, "app.log")


def mask_sensitive_data(text: str) -> str:
    """Redact bearer tokens, API keys/secrets, and email addresses in the given text."""
    if not isinstance(text, str):
        return text
    for regex, replacement in SENSITIVE_REGEXES:
        text = regex.sub(replacement, text)
    return text


def clean_user_friendly_message(msg: str) -> str:
    """Clean technical tool call details from log message for user-friendly terminal output.
    
    For example:
        'Agent turn 1/10: calling tool list_dir: Listing directory contents'
    becomes:
        'Listing directory contents'
    """
    if not isinstance(msg, str):
        return msg

    # Strip "Agent turn X/Y: " prefix if present
    cleaned = re.sub(r"^Agent turn \d+/\d+:\s*", "", msg)

    # Check standalone "calling tool <name>: <purpose>"
    match_single = re.match(r"^calling tool\s+[a-zA-Z0-9_-]+:\s*(.*)$", cleaned, re.IGNORECASE)
    if match_single:
        return match_single.group(1)

    # Check standalone "calling tools <name1>: <purpose1>; <name2>: <purpose2>"
    match_multi = re.match(r"^calling tools\s+(.*)$", cleaned, re.IGNORECASE)
    if match_multi:
        raw_tools = match_multi.group(1)
        parts = raw_tools.split("; ")
        cleaned_parts = [re.sub(r"^[a-zA-Z0-9_-]+:\s*", "", p) for p in parts]
        return "; ".join(cleaned_parts)

    # Handle "[calling tool <name>: <purpose>]" inside bracketed narration
    def _clean_bracket_tool_call(m: re.Match) -> str:
        content = m.group(1)
        return f"[{clean_user_friendly_message(content)}]"

    cleaned = re.sub(r"\[(calling tool[s]?\s+[^\]]+)\]", _clean_bracket_tool_call, cleaned, flags=re.IGNORECASE)

    # Remove lingering "calling tool <name>:" or "calling tools" technical prefixes
    cleaned = re.sub(r"calling tool\s+[a-zA-Z0-9_-]+:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"calling tools\s*", "", cleaned, flags=re.IGNORECASE)

    return cleaned


class MaskingJsonFormatter(logging.Formatter):
    """Log formatter that outputs JSON logs with automated PII and credential masking."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_sensitive_data(record.getMessage()),
        }

        # Include custom extra fields passed via extra={...}
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime"
        }
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                if isinstance(val, str):
                    log_obj[key] = mask_sensitive_data(val)
                else:
                    log_obj[key] = val

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


class UserFriendlyFormatter(logging.Formatter):
    """Formatter that outputs clean, user-friendly plain text for non-technical terminal users."""

    def format(self, record: logging.LogRecord) -> str:
        msg = mask_sensitive_data(record.getMessage())
        msg = clean_user_friendly_message(msg)
        if record.levelno >= logging.ERROR:
            return f"[Error] {msg}"
        elif record.levelno >= logging.WARNING:
            return f"[Warning] {msg}"
        return msg


class ConsoleFilter(logging.Filter):
    """Filter to suppress file-only logs (such as parallel tool execution details) from terminal output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "file_only", False):
            return False
        msg = record.getMessage()
        if "[Orchestrator] Executing" in msg:
            return False
        if "Executing" in msg and "in parallel" in msg:
            return False
        return True


def get_audit_logger(name: str, log_file: Optional[str] = None) -> logging.Logger:
    """Return a named logger configured with a file handler for detailed JSON logs and a console handler for user-friendly output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        target_log_file = log_file or DEFAULT_LOG_FILE
        os.makedirs(os.path.dirname(target_log_file), exist_ok=True)

        # File Handler: Developer structured JSON logs stored in log/ folder
        file_handler = logging.FileHandler(target_log_file, encoding="utf-8")
        file_handler.setFormatter(MaskingJsonFormatter())
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

        # Terminal Handler: Non-tech user-friendly clean output
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(UserFriendlyFormatter())
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(ConsoleFilter())
        logger.addHandler(console_handler)

        logger.setLevel(logging.INFO)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Alias for get_audit_logger to maintain backward compatibility."""
    return get_audit_logger(name)


__all__ = [
    "SENSITIVE_REGEXES",
    "mask_sensitive_data",
    "clean_user_friendly_message",
    "MaskingJsonFormatter",
    "UserFriendlyFormatter",
    "ConsoleFilter",
    "get_audit_logger",
    "get_logger",
]

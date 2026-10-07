"""Tool output sanitizer for prompt injection detection and XML encapsulation."""

from __future__ import annotations

import html
import re

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s+override",
    r"you\s+are\s+now\s+an?\s+unrestricted",
    r"disregard\s+above",
]


def sanitize_tool_output(content: str, path: str = "") -> str:
    """Detect prompt injection keywords and encapsulate content in strict XML tags."""
    # Check for suspicious injection patterns
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            content = f"[SECURITY WARNING: Possible prompt injection detected in file content]\n" + content
            break

    # Escape XML tags inside content to avoid breaking boundaries
    safe_content = content.replace("</untrusted_file_content>", "&lt;/untrusted_file_content&gt;")

    return (
        f'<untrusted_file_content path="{html.escape(path)}">\n'
        f"{safe_content}\n"
        f"</untrusted_file_content>"
    )


__all__ = ["INJECTION_PATTERNS", "sanitize_tool_output"]

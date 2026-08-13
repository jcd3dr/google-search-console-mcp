"""Output sanitization for LLM safety.

Ensures all API responses are JSON-serialized and strips content
that could be interpreted as LLM instructions.
"""

import json
import re
from typing import Any


# Patterns that could be prompt injection attempts
_INJECTION_PATTERNS = re.compile(
    r"(system:|assistant:|human:|<\|im_start\|>|<\|im_end\|>|"
    r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>)",
    re.IGNORECASE,
)


def sanitize_string(value: str) -> str:
    """Remove potential prompt injection patterns from a string."""
    return _INJECTION_PATTERNS.sub("[filtered]", value)


def sanitize_value(value: Any) -> Any:
    """Recursively sanitize a value."""
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, dict):
        return {k: sanitize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


def format_response(tool: str, data: Any, metadata: dict[str, Any] | None = None) -> str:
    """Format a tool response as sanitized JSON.

    Args:
        tool: Name of the tool that produced this response.
        data: The API response data.
        metadata: Optional metadata (pagination info, etc).

    Returns:
        JSON string safe for LLM consumption.
    """
    response = {
        "tool": tool,
        "data": sanitize_value(data),
    }
    if metadata:
        response["metadata"] = sanitize_value(metadata)
    return json.dumps(response, indent=2, default=str)


def format_error(tool: str, message: str) -> str:
    """Format an error response, stripping any credential info."""
    # Remove anything that looks like a token or key
    clean = re.sub(r"(ya29\.[^\s]+|token=[^\s&]+|key=[^\s&]+)", "[REDACTED]", message)
    return json.dumps({
        "tool": tool,
        "error": sanitize_string(clean),
    }, indent=2)
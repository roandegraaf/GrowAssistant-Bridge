"""
Input Validation Utilities.

This module provides validation functions for user input and API data
to prevent security issues like injection attacks.
"""

import re
from typing import Any

# Safe patterns for common input types
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
SAFE_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\./]+$")
UUID_PATTERN = re.compile(r"^[a-fA-F0-9\-]{36}$")
URL_PATTERN = re.compile(
    r"^https?://"  # http:// or https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
    r"localhost|"  # localhost
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # or IP
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def validate_name(name: str, max_length: int = 255) -> tuple[bool, str | None]:
    """Validate a name/identifier string.

    Args:
        name: The name to validate.
        max_length: Maximum allowed length.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not name:
        return False, "Name cannot be empty"

    if len(name) > max_length:
        return False, f"Name exceeds maximum length of {max_length}"

    if not SAFE_NAME_PATTERN.match(name):
        return (
            False,
            "Name contains invalid characters (only alphanumeric, underscore, hyphen, and dot allowed)",
        )

    return True, None


def validate_path(path: str, max_length: int = 1024) -> tuple[bool, str | None]:
    """Validate a file path string.

    Args:
        path: The path to validate.
        max_length: Maximum allowed length.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not path:
        return False, "Path cannot be empty"

    if len(path) > max_length:
        return False, f"Path exceeds maximum length of {max_length}"

    # Check for path traversal attempts
    if ".." in path:
        return False, "Path traversal not allowed"

    if not SAFE_PATH_PATTERN.match(path):
        return False, "Path contains invalid characters"

    return True, None


def validate_uuid(uuid_str: str) -> tuple[bool, str | None]:
    """Validate a UUID string.

    Args:
        uuid_str: The UUID string to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not uuid_str:
        return False, "UUID cannot be empty"

    if not UUID_PATTERN.match(uuid_str):
        return False, "Invalid UUID format"

    return True, None


def validate_integer(
    value: Any, min_value: int | None = None, max_value: int | None = None
) -> tuple[bool, str | None, int | None]:
    """Validate and convert an integer value.

    Args:
        value: The value to validate.
        min_value: Minimum allowed value.
        max_value: Maximum allowed value.

    Returns:
        Tuple of (is_valid, error_message, converted_value).
    """
    try:
        int_value = int(value)
    except (ValueError, TypeError):
        return False, "Value must be an integer", None

    if min_value is not None and int_value < min_value:
        return False, f"Value must be at least {min_value}", None

    if max_value is not None and int_value > max_value:
        return False, f"Value must be at most {max_value}", None

    return True, None, int_value


def validate_float(
    value: Any, min_value: float | None = None, max_value: float | None = None
) -> tuple[bool, str | None, float | None]:
    """Validate and convert a float value.

    Args:
        value: The value to validate.
        min_value: Minimum allowed value.
        max_value: Maximum allowed value.

    Returns:
        Tuple of (is_valid, error_message, converted_value).
    """
    try:
        float_value = float(value)
    except (ValueError, TypeError):
        return False, "Value must be a number", None

    if min_value is not None and float_value < min_value:
        return False, f"Value must be at least {min_value}", None

    if max_value is not None and float_value > max_value:
        return False, f"Value must be at most {max_value}", None

    return True, None, float_value


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """Sanitize a string by removing potentially dangerous characters.

    Args:
        value: The string to sanitize.
        max_length: Maximum allowed length.

    Returns:
        Sanitized string.
    """
    if not value:
        return ""

    # Truncate to max length
    sanitized = value[:max_length]

    # Remove null bytes and control characters (except newlines and tabs)
    sanitized = "".join(char for char in sanitized if ord(char) >= 32 or char in "\n\t\r")

    return sanitized.strip()


def validate_url(url: str) -> tuple[bool, str | None]:
    """Validate a URL string.

    Args:
        url: The URL to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not url:
        return False, "URL cannot be empty"

    if not URL_PATTERN.match(url):
        return False, "Invalid URL format"

    return True, None

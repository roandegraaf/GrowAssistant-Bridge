"""
Sensitive Data Masking Utilities.

This module provides utilities for masking and unmasking sensitive data
in configuration and log outputs to prevent accidental exposure.
"""

from copy import deepcopy
from typing import Any, Optional

# Default mask string used to replace sensitive values
DEFAULT_MASK = "**********"

# Default paths to sensitive fields (dot notation for nested keys)
DEFAULT_SENSITIVE_PATHS: set[str] = {
    "api.auth_token",
    "web.password_hash",
    "web.secret_key",
    "credentials.token",
    "credentials.password",
}

# Keys that should always be masked regardless of path
SENSITIVE_KEYS: set[str] = {
    "password",
    "password_hash",
    "secret",
    "secret_key",
    "auth_token",
    "api_key",
    "token",
    "private_key",
}


def mask_sensitive_data(
    data: dict[str, Any],
    mask: str = DEFAULT_MASK,
    sensitive_paths: Optional[set[str]] = None,
    sensitive_keys: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Mask sensitive data in a dictionary.

    This function creates a deep copy and replaces sensitive values
    with a mask string to prevent accidental exposure in logs or UI.

    Args:
        data: The dictionary to mask sensitive values in.
        mask: The string to replace sensitive values with.
        sensitive_paths: Set of dot-notation paths to sensitive fields.
        sensitive_keys: Set of key names that should always be masked.

    Returns:
        A deep copy of the dictionary with sensitive values masked.

    Example:
        >>> config = {"api": {"auth_token": "secret123"}, "web": {"host": "0.0.0.0"}}
        >>> masked = mask_sensitive_data(config)
        >>> masked["api"]["auth_token"]
        '**********'
        >>> masked["web"]["host"]
        '0.0.0.0'
    """
    if sensitive_paths is None:
        sensitive_paths = DEFAULT_SENSITIVE_PATHS
    if sensitive_keys is None:
        sensitive_keys = SENSITIVE_KEYS

    result = deepcopy(data)
    _mask_recursive(result, sensitive_paths, sensitive_keys, mask, "")
    return result


def _mask_recursive(
    data: Any,
    sensitive_paths: set[str],
    sensitive_keys: set[str],
    mask: str,
    current_path: str,
) -> None:
    """Recursively mask sensitive values in a dictionary.

    Args:
        data: The data structure to process (modified in place).
        sensitive_paths: Set of paths that should be masked.
        sensitive_keys: Set of keys that should always be masked.
        mask: The mask string to use.
        current_path: The current dot-notation path being processed.
    """
    if not isinstance(data, dict):
        return

    for key, value in data.items():
        # Build the full path for this key
        full_path = f"{current_path}.{key}" if current_path else key

        # Check if this key or path should be masked
        should_mask = (
            key.lower() in {k.lower() for k in sensitive_keys} or full_path in sensitive_paths
        )

        if should_mask and value is not None and value != "":
            data[key] = mask
        elif isinstance(value, dict):
            _mask_recursive(value, sensitive_paths, sensitive_keys, mask, full_path)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    item_path = f"{full_path}[{i}]"
                    _mask_recursive(item, sensitive_paths, sensitive_keys, mask, item_path)


def unmask_sensitive_data(
    masked_data: dict[str, Any],
    original_data: dict[str, Any],
    mask: str = DEFAULT_MASK,
) -> dict[str, Any]:
    """Restore masked values from original data.

    This function is useful when updating configuration - it preserves
    the original sensitive values when the masked placeholder is detected.

    Args:
        masked_data: The data with potentially masked values.
        original_data: The original data with actual sensitive values.
        mask: The mask string used to identify masked values.

    Returns:
        A copy of masked_data with masked values restored from original_data.

    Example:
        >>> original = {"api": {"auth_token": "secret123", "url": "http://old.com"}}
        >>> update = {"api": {"auth_token": "**********", "url": "http://new.com"}}
        >>> result = unmask_sensitive_data(update, original)
        >>> result["api"]["auth_token"]
        'secret123'
        >>> result["api"]["url"]
        'http://new.com'
    """
    result = deepcopy(masked_data)
    _unmask_recursive(result, original_data, mask)
    return result


def _unmask_recursive(
    data: Any,
    original: Any,
    mask: str,
) -> None:
    """Recursively restore masked values from original data.

    Args:
        data: The data structure to process (modified in place).
        original: The original data with actual values.
        mask: The mask string to detect.
    """
    if not isinstance(data, dict) or not isinstance(original, dict):
        return

    for key, value in data.items():
        if key not in original:
            continue

        original_value = original[key]

        if value == mask:
            # Restore the original value
            data[key] = original_value
        elif isinstance(value, dict) and isinstance(original_value, dict):
            _unmask_recursive(value, original_value, mask)
        elif isinstance(value, list) and isinstance(original_value, list):
            for i, item in enumerate(value):
                if i < len(original_value) and isinstance(item, dict):
                    _unmask_recursive(item, original_value[i], mask)


def get_safe_config_for_logging(
    config_data: dict[str, Any],
    additional_masks: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Get a config dictionary that's safe for logging.

    This is a convenience function that masks common sensitive fields
    and any additional fields specified.

    Args:
        config_data: The configuration dictionary.
        additional_masks: Additional dot-notation paths to mask.

    Returns:
        A copy of the config with sensitive values masked.
    """
    sensitive_paths = DEFAULT_SENSITIVE_PATHS.copy()
    if additional_masks:
        sensitive_paths.update(additional_masks)

    return mask_sensitive_data(config_data, sensitive_paths=sensitive_paths)

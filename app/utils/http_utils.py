"""
HTTP Utilities Module.

This module provides shared HTTP utility functions for building
headers and handling common HTTP operations across the application.
"""

from typing import Dict, Optional


def build_headers(
    content_type: str = "application/json",
    accept: str = "application/json",
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build standard HTTP headers for API requests.

    Args:
        content_type: Content-Type header value. Defaults to "application/json".
        accept: Accept header value. Defaults to "application/json".
        extra_headers: Additional headers to include.

    Returns:
        Dictionary of HTTP headers.
    """
    headers = {
        "Content-Type": content_type,
        "Accept": accept,
    }

    if extra_headers:
        headers.update(extra_headers)

    return headers


def build_auth_headers(
    client_id: Optional[str] = None,
    token: Optional[str] = None,
    content_type: str = "application/json",
    accept: str = "application/json",
) -> Dict[str, str]:
    """Build HTTP headers with authentication.

    Args:
        client_id: Client ID for X-Client-ID header.
        token: Bearer token for Authorization header.
        content_type: Content-Type header value.
        accept: Accept header value.

    Returns:
        Dictionary of HTTP headers including auth headers if provided.
    """
    headers = build_headers(content_type, accept)

    if client_id:
        headers["X-Client-ID"] = client_id

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def get_client_headers(
    auth_manager, include_client_id: bool = True, include_token: bool = False
) -> Dict[str, str]:
    """Build headers using auth manager credentials.

    This is a convenience function that integrates with the AuthManager.

    Args:
        auth_manager: The AuthManager instance to get credentials from.
        include_client_id: Whether to include the X-Client-ID header.
        include_token: Whether to include the Authorization header.

    Returns:
        Dictionary of HTTP headers.
    """
    headers = build_headers()

    if include_client_id and auth_manager.is_authenticated():
        client_id = auth_manager.get_client_id()
        if client_id:
            headers["X-Client-ID"] = client_id

    if include_token:
        credentials = getattr(auth_manager, "_credentials", None)
        if credentials and "token" in credentials:
            headers["Authorization"] = f"Bearer {credentials['token']}"

    return headers

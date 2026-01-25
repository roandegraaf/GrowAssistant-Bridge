"""
Shared fixtures for all tests.

This module provides common fixtures and utilities used across all test modules.
It handles proper isolation of singleton instances and provides mock objects
for external dependencies.
"""

import asyncio
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# =============================================================================
# Singleton Reset Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singleton instances before and after each test.

    This ensures test isolation by clearing cached singleton instances.
    """
    from app.utils.singleton import SingletonMeta

    # Clear all singleton instances before the test
    with SingletonMeta._lock:
        SingletonMeta._instances.clear()

    yield

    # Clear all singleton instances after the test
    with SingletonMeta._lock:
        SingletonMeta._instances.clear()


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def sample_config() -> dict[str, Any]:
    """Provide a sample configuration dictionary."""
    return {
        "api": {
            "url": "http://localhost:8080",
            "batch_size": 100,
            "poll_interval": 5,
            "transmission_interval": 10,
            "timeout": 30,
            "retry_max_attempts": 3,
            "retry_min_backoff": 1,
            "retry_max_backoff": 10,
            "log_values": False,
            "verify_ssl": True,
        },
        "general": {
            "collection_interval": 10,
            "log_level": "INFO",
            "data_dir": "data",
            "log_file": "logs/app.log",
            "external_integrations_dir": "external_integrations",
        },
        "integrations": {
            "sample": {
                "enabled": True,
                "devices": {
                    "temp1": {"name": "temperature_sensor", "type": "temperature"},
                    "pump1": {"name": "water_pump", "type": "pump"},
                },
            },
        },
        "queue": {
            "persistence_enabled": False,
            "max_queue_size": 1000,
            "flush_interval": 60,
        },
        "web": {
            "enabled": False,
            "port": 5010,
            "auth_enabled": False,
        },
    }


@pytest.fixture
def temp_config_file(sample_config: dict[str, Any], tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary config file for testing."""
    config_file = tmp_path / "config.yaml"

    with open(config_file, "w") as f:
        yaml.dump(sample_config, f)

    yield config_file


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary data directory for testing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    yield data_dir


@pytest.fixture
def mock_config(sample_config: dict[str, Any]):
    """Provide a mocked Config instance."""
    mock = MagicMock()
    mock.config = sample_config
    mock.config_file = "config.yaml"

    def get_side_effect(key: str, default: Any = None) -> Any:
        parts = key.split(".")
        value = sample_config
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    mock.get.side_effect = get_side_effect
    mock.get_section.side_effect = lambda section: sample_config.get(section, {})

    return mock


# =============================================================================
# HTTP Client Fixtures
# =============================================================================


@pytest.fixture
def mock_httpx_client():
    """Provide a mocked httpx AsyncClient."""
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    mock_client.aclose = AsyncMock()
    return mock_client


@pytest.fixture
def mock_httpx_response():
    """Provide a factory for mock HTTP responses."""

    def create_response(
        status_code: int = 200,
        json_data: dict[str, Any] = None,
        text: str = "",
        raise_error: bool = False,
    ):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_data or {}
        mock_response.text = text

        if raise_error and status_code >= 400:
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"HTTP {status_code}", request=MagicMock(), response=mock_response
            )
        else:
            mock_response.raise_for_status = MagicMock()

        return mock_response

    return create_response


# =============================================================================
# Queue Manager Fixtures
# =============================================================================


@pytest.fixture
def sample_data_point() -> dict[str, Any]:
    """Provide a sample data point."""
    return {
        "timestamp": 1700000000000,
        "integration": "sample",
        "type": "temperature",
        "value": 25.5,
        "device": "temp1",
    }


@pytest.fixture
def sample_data_points() -> list:
    """Provide a list of sample data points."""
    return [
        {"timestamp": 1700000000000, "integration": "sample", "type": "temperature", "value": 25.5},
        {"timestamp": 1700000001000, "integration": "sample", "type": "humidity", "value": 60.0},
        {"timestamp": 1700000002000, "integration": "sample", "type": "ph", "value": 6.5},
    ]


# =============================================================================
# Registry Fixtures
# =============================================================================


@pytest.fixture
def mock_registry():
    """Provide a fresh DeviceRegistry instance for testing."""
    from app.registry import DeviceCategory, DeviceRegistry
    from app.utils.singleton import SingletonMeta

    # Reset the singleton first
    if DeviceRegistry in SingletonMeta._instances:
        del SingletonMeta._instances[DeviceRegistry]

    registry = DeviceRegistry()

    # Register some test devices
    registry.register_device(
        name="temp_sensor",
        domain="test",
        device_type="temperature",
        category=DeviceCategory.SENSOR,
        integration_name="TestIntegration",
    )
    registry.register_device(
        name="pump1",
        domain="test",
        device_type="pump",
        category=DeviceCategory.ACTUATOR,
        integration_name="TestIntegration",
    )

    return registry


# =============================================================================
# API Types Fixtures
# =============================================================================


@pytest.fixture
def sample_api_response() -> dict[str, Any]:
    """Provide a sample API response."""
    return {
        "rdhMode": False,
        "status": "active",
        "light": {"day": {"on": "06:00", "off": "22:00"}, "night": {}},
        "climate": {"temperature": 25, "humidity": 60, "baseFanSpeed": 50},
        "tank": {"waters": [], "ph": 6.5, "amountML": 1000},
        "actions": [
            {
                "id": "action-1",
                "type": "LIGHT",
                "value": "on",
                "pumpNumber": None,
                "received": False,
                "resolved": False,
            }
        ],
    }


@pytest.fixture
def sample_action() -> dict[str, Any]:
    """Provide a sample action."""
    return {
        "id": "test-action-1",
        "type": "LIGHT",
        "value": "on",
        "pumpNumber": None,
    }


# =============================================================================
# Integration Fixtures
# =============================================================================


@pytest.fixture
def sample_integration_config() -> dict[str, Any]:
    """Provide a sample integration configuration."""
    return {
        "enabled": True,
        "devices": {
            "temp1": {"name": "temperature_sensor", "type": "temperature"},
            "humid1": {"name": "humidity_sensor", "type": "humidity"},
            "pump1": {"name": "water_pump", "type": "pump"},
        },
    }


# =============================================================================
# Web App Fixtures
# =============================================================================


@pytest.fixture
def flask_test_client(mock_config):
    """Provide a Flask test client."""
    from web.app import app

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test-secret-key"

    # Mock the APPLICATION_INSTANCE
    mock_app_instance = MagicMock()
    mock_app_instance._integrations = {}
    mock_app_instance.loop = None
    app.config["APPLICATION_INSTANCE"] = mock_app_instance

    with app.test_client() as client:
        with app.app_context():
            yield client


@pytest.fixture
def authenticated_flask_client(flask_test_client):
    """Provide an authenticated Flask test client."""
    with flask_test_client.session_transaction() as sess:
        sess["logged_in"] = True
    return flask_test_client


# =============================================================================
# Async Utilities
# =============================================================================


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Environment Fixtures
# =============================================================================


@pytest.fixture
def clean_environment():
    """Provide a clean environment without CANNABIS_GROW_* variables."""
    original_env = os.environ.copy()

    # Remove any CANNABIS_GROW_* environment variables
    for key in list(os.environ.keys()):
        if key.startswith("CANNABIS_GROW_"):
            del os.environ[key]

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_env_vars():
    """Provide environment variable mocking context manager."""

    def set_env_vars(**kwargs):
        original = {k: os.environ.get(k) for k in kwargs}

        for key, value in kwargs.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)

        return original

    def restore_env_vars(original):
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    class EnvContext:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.original = None

        def __enter__(self):
            self.original = set_env_vars(**self.kwargs)
            return self

        def __exit__(self, *args):
            if self.original:
                restore_env_vars(self.original)

    return EnvContext


# =============================================================================
# Credential Fixtures
# =============================================================================


@pytest.fixture
def sample_credentials() -> dict[str, Any]:
    """Provide sample authentication credentials."""
    return {
        "client_id": "test-client-123",
        "custom_id": "test-host-abc123",
        "registration_time": "123456789.0",
        "connected": True,
        "ready": True,
    }


@pytest.fixture
def temp_credentials_file(
    sample_credentials: dict[str, Any], tmp_path: Path
) -> Generator[Path, None, None]:
    """Create a temporary credentials file for testing."""
    import json

    credentials_file = tmp_path / "credentials.json"

    with open(credentials_file, "w") as f:
        json.dump(sample_credentials, f)

    yield credentials_file


# =============================================================================
# Application Fixtures
# =============================================================================


@pytest.fixture
def mock_integration():
    """Mock integration instance for Application tests."""
    integration = AsyncMock()
    integration.name = "test_integration"
    integration.connect = AsyncMock(return_value=True)
    integration.disconnect = AsyncMock()
    integration.execute_command = AsyncMock(return_value=True)
    integration.apply_settings = AsyncMock()
    integration.register_capabilities = MagicMock()

    async def receive_data_generator():
        yield {"type": "temperature", "value": 25.5, "device": "sensor1"}

    integration.receive_data = MagicMock(return_value=receive_data_generator())
    return integration


@pytest.fixture
def mock_application_dependencies(mock_config, mock_httpx_client):
    """Set up all mocked dependencies for Application tests.

    Returns a dictionary containing all mocked singletons and dependencies
    needed for testing the Application class.
    """
    from unittest.mock import patch

    mocks = {}

    # Mock auth_manager
    mock_auth = AsyncMock()
    mock_auth.start = AsyncMock()
    mock_auth.stop = AsyncMock()
    mock_auth.is_authenticated = MagicMock(return_value=True)
    mock_auth.is_ready_for_data = MagicMock(return_value=True)
    mock_auth.validate_credentials = AsyncMock(return_value=True)
    mock_auth.register_client = AsyncMock(return_value=True)
    mock_auth.wait_for_connection = AsyncMock(return_value=True)
    mock_auth.wait_for_space_creation = AsyncMock(return_value=True)
    mock_auth.check_connection_status = AsyncMock(return_value=(True, "ready"))
    mock_auth.display_auth_code = MagicMock()
    mocks["auth_manager"] = mock_auth

    # Mock api_client
    mock_api = AsyncMock()
    mock_api.start = AsyncMock()
    mock_api.stop = AsyncMock()
    mock_api.start_command_polling = AsyncMock()
    mock_api.register_settings_callback = MagicMock()
    mock_api.send_data = AsyncMock(return_value=(True, "Success"))
    mock_api.get_command = AsyncMock(return_value=None)
    mock_api.send_command_result = AsyncMock()
    mocks["api_client"] = mock_api

    # Mock queue_manager
    mock_queue = AsyncMock()
    mock_queue.start = AsyncMock()
    mock_queue.stop = AsyncMock()
    mock_queue.put = AsyncMock()
    mock_queue.get_data_points = AsyncMock(return_value=[])
    mock_queue.mark_processed = AsyncMock()
    mock_queue.requeue_data_points = AsyncMock()
    mocks["queue_manager"] = mock_queue

    # Mock registry
    mock_registry = MagicMock()
    mock_registry.get_sensor_integration = MagicMock(return_value=None)
    mock_registry.get_actuator_integration = MagicMock(return_value=None)
    mocks["registry"] = mock_registry

    # Mock config
    mocks["config"] = mock_config

    return mocks

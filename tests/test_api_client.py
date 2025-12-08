"""
Tests for the ApiClient module.

This module tests API communication, data transmission, command polling,
retry logic, and response processing with mocked HTTP.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api_types import ActionType, LogType, ProblemStatus, ProblemType


class TestApiClient:
    """Tests for the ApiClient class."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a fresh ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_auth.is_ready_for_data.return_value = True

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            yield client

    @pytest.fixture
    def started_api_client(self, api_client, mock_httpx_client):
        """Create a started ApiClient with mocked HTTP client."""

        async def setup():
            with patch.object(httpx, "AsyncClient", return_value=mock_httpx_client):
                await api_client.start()
                api_client._client = mock_httpx_client
            return api_client

        return asyncio.get_event_loop().run_until_complete(setup())

    def test_api_client_initialization(self, api_client):
        """Test ApiClient initialization."""
        assert api_client._client is None
        assert api_client._data_logs == []
        assert api_client._problems == []
        assert api_client._actions == []

    @pytest.mark.asyncio
    async def test_start_creates_client(self, api_client):
        """Test that start creates HTTP client."""
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            await api_client.start()

            assert api_client._client is mock_client
            assert api_client._command_queue is not None

            await api_client.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_client(self, api_client, mock_httpx_client):
        """Test that stop closes HTTP client."""
        api_client._client = mock_httpx_client

        await api_client.stop()

        mock_httpx_client.aclose.assert_called_once()
        assert api_client._client is None


class TestApiClientDataMethods:
    """Tests for ApiClient data methods."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a fresh ApiClient instance."""
        with patch("app.api_client.config", mock_config), patch("app.api_client.auth_manager"):
            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            yield client

    def test_add_data_log(self, api_client):
        """Test adding a data log."""
        api_client.add_data_log(LogType.TEMPERATURE, 25.5)

        assert len(api_client._data_logs) == 1
        assert api_client._data_logs[0]["logType"] == "TEMPERATURE"
        assert api_client._data_logs[0]["value"] == "25.5"

    def test_add_data_log_with_pump_num(self, api_client):
        """Test adding a data log with pump number."""
        api_client.add_data_log(LogType.SUPPLEMENT_ML, 100, pump_num=1)

        assert api_client._data_logs[0]["pumpNum"] == 1

    def test_add_data_log_with_custom_date(self, api_client):
        """Test adding a data log with custom date."""
        custom_date = datetime(2024, 1, 15, 12, 0, 0)
        api_client.add_data_log(LogType.HUMIDITY, 60.0, log_date=custom_date)

        assert "2024-01-15" in api_client._data_logs[0]["logDate"]

    def test_add_problem(self, api_client):
        """Test adding a problem."""
        api_client.add_problem(
            problem_type=ProblemType.TEMPERATURE,
            status=ProblemStatus.RANGE,
            description="Temperature too high",
            priority=70,
        )

        assert len(api_client._problems) == 1
        assert api_client._problems[0]["type"] == "TEMPERATURE"
        assert api_client._problems[0]["status"] == "RANGE"
        assert api_client._problems[0]["priority"] == 70

    def test_acknowledge_action(self, api_client):
        """Test acknowledging an action."""
        api_client.acknowledge_action("action-123", received=True, resolved=False)

        assert len(api_client._actions) == 1
        assert api_client._actions[0]["id"] == "action-123"
        assert api_client._actions[0]["received"] is True
        assert api_client._actions[0]["resolved"] is False

    def test_register_action_handler(self, api_client):
        """Test registering an action handler."""
        handler = MagicMock()
        api_client.register_action_handler(ActionType.LIGHT, handler)

        assert "LIGHT" in api_client._action_handlers
        assert api_client._action_handlers["LIGHT"] is handler

    def test_register_settings_callback(self, api_client):
        """Test registering a settings callback."""
        callback = AsyncMock()
        api_client.register_settings_callback(callback)

        assert api_client._settings_callback is callback


class TestApiClientSendData:
    """Tests for ApiClient send_data method."""

    @pytest.fixture
    def api_client(self, mock_config, mock_httpx_response):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_auth.is_ready_for_data.return_value = True

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()

            # Set up mock HTTP client
            mock_client = AsyncMock()
            mock_response = mock_httpx_response(
                status_code=200, json_data={"status": "ok", "actions": []}
            )
            mock_client.post.return_value = mock_response
            mock_client.aclose = AsyncMock()
            client._client = mock_client
            client._command_queue = asyncio.Queue()

            yield client

    @pytest.mark.asyncio
    async def test_send_data_success(self, api_client):
        """Test successful data transmission."""
        api_client.add_data_log(LogType.TEMPERATURE, 25.5)

        success, message = await api_client.send_data()

        assert success is True
        assert "successfully" in message.lower()
        assert len(api_client._data_logs) == 0  # Should be cleared

    @pytest.mark.asyncio
    async def test_send_data_clears_after_success(self, api_client):
        """Test that data is cleared after successful send."""
        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        api_client.add_problem(ProblemType.HUMIDITY, ProblemStatus.RANGE, "Test")
        api_client.acknowledge_action("action-1", True, False)

        await api_client.send_data()

        assert len(api_client._data_logs) == 0
        assert len(api_client._problems) == 0
        assert len(api_client._actions) == 0

    @pytest.mark.asyncio
    async def test_send_data_not_started(self, mock_config):
        """Test send_data when client not started."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = True

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            success, message = await client.send_data()

            assert success is False
            assert "not started" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_not_authenticated(self, mock_config, mock_httpx_client):
        """Test send_data when not authenticated."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = False

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            client._client = mock_httpx_client

            success, message = await client.send_data()

            assert success is False
            assert "authenticated" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_with_legacy_data_points(self, api_client):
        """Test send_data with legacy data points format."""
        legacy_points = [
            {"timestamp": 1700000000000, "type": "TEMPERATURE", "value": 25.5},
            {"timestamp": 1700000001000, "type": "HUMIDITY", "value": 60.0},
        ]

        success, _ = await api_client.send_data(data_points=legacy_points)

        assert success is True


class TestApiClientPollCommands:
    """Tests for ApiClient command polling."""

    @pytest.fixture
    async def api_client(self, mock_config, mock_httpx_response):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()

            # Set up mock HTTP client
            mock_client = AsyncMock()
            client._client = mock_client
            # Create queue in the current event loop
            client._command_queue = asyncio.Queue()
            client._mock_httpx_response = mock_httpx_response

            yield client

    @pytest.mark.asyncio
    async def test_poll_commands_returns_commands(self, api_client):
        """Test polling commands returns command list."""
        mock_response = api_client._mock_httpx_response(
            status_code=200,
            json_data={
                "commands": [
                    {"id": "cmd-1", "action": "on", "target": "pump1"},
                    {"id": "cmd-2", "action": "off", "target": "pump2"},
                ]
            },
        )
        api_client._client.get.return_value = mock_response

        commands = await api_client.poll_commands()

        assert len(commands) == 2
        assert commands[0]["id"] == "cmd-1"

    @pytest.mark.asyncio
    async def test_poll_commands_204_returns_empty(self, api_client):
        """Test polling with 204 status returns empty list."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()
        api_client._client.get.return_value = mock_response

        commands = await api_client.poll_commands()

        assert commands == []

    @pytest.mark.asyncio
    async def test_poll_commands_not_authenticated(self, mock_config):
        """Test poll_commands when not authenticated."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
        ):
            mock_auth.is_authenticated.return_value = False

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            client._client = MagicMock()

            commands = await client.poll_commands()

            assert commands is None

    @pytest.mark.asyncio
    async def test_get_command_from_queue(self, api_client):
        """Test getting command from queue."""
        command = {"id": "test-cmd", "action": "test"}
        await api_client._command_queue.put(command)

        result = await api_client.get_command(timeout=1.0)

        assert result == command

    @pytest.mark.asyncio
    async def test_get_command_timeout(self, api_client):
        """Test get_command returns None on timeout."""
        result = await api_client.get_command(timeout=0.1)

        assert result is None


class TestApiClientProcessResponse:
    """Tests for ApiClient response processing."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with patch("app.api_client.config", mock_config), patch("app.api_client.auth_manager"):
            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            yield client

    @pytest.mark.asyncio
    async def test_process_response_calls_settings_callback(self, api_client, sample_api_response):
        """Test that process_response calls settings callback."""
        callback = AsyncMock()
        api_client.register_settings_callback(callback)

        await api_client._process_response(sample_api_response)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_response_handles_actions(self, api_client, sample_api_response):
        """Test that process_response handles actions."""
        handler = AsyncMock(return_value=True)
        api_client.register_action_handler("LIGHT", handler)

        await api_client._process_response(sample_api_response)

        handler.assert_called_once()
        # Should have acknowledged the action twice (received and resolved)
        assert len(api_client._actions) >= 1

    @pytest.mark.asyncio
    async def test_process_response_action_without_handler(self, api_client):
        """Test process_response with action but no handler."""
        response = {"actions": [{"id": "action-1", "type": "UNKNOWN_TYPE", "value": "test"}]}

        await api_client._process_response(response)

        # Should still acknowledge as received
        assert len(api_client._actions) == 1
        assert api_client._actions[0]["received"] is True
        assert api_client._actions[0]["resolved"] is False


class TestApiClientProblemDetection:
    """Tests for ApiClient problem detection from data."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with patch("app.api_client.config", mock_config), patch("app.api_client.auth_manager"):
            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            yield client

    def test_detect_temperature_out_of_range(self, api_client):
        """Test detection of temperature out of range."""
        data_points = [{"type": "TEMPERATURE", "value": 100, "integration": "test"}]  # Way too high

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 1
        assert api_client._problems[0]["type"] == "TEMPERATURE"
        assert api_client._problems[0]["status"] == "RANGE"

    def test_detect_sensor_failure(self, api_client):
        """Test detection of sensor failure."""
        data_points = [{"type": "HUMIDITY", "value": "error", "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 1
        assert api_client._problems[0]["status"] == "CONNECTION"

    def test_no_problem_for_valid_values(self, api_client):
        """Test no problem detected for valid values."""
        data_points = [
            {"type": "TEMPERATURE", "value": 25, "integration": "test"},
            {"type": "HUMIDITY", "value": 60, "integration": "test"},
            {"type": "PH", "value": 6.5, "integration": "test"},
        ]

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 0

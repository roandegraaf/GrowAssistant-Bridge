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
    async def test_sse_action_event_queues_command(self, api_client):
        """Test that SSE action events are queued as commands."""
        action_payload = {"id": "cmd-1", "type": "LIGHT", "action": "on", "target": "pump1"}
        await api_client._handle_action_event(action_payload)

        result = await api_client.get_command(timeout=1.0)
        assert result["id"] == "cmd-1"

    @pytest.mark.asyncio
    async def test_sse_action_event_missing_id(self, api_client):
        """Test that SSE action events without id are ignored."""
        action_payload = {"type": "LIGHT", "action": "on"}
        await api_client._handle_action_event(action_payload)

        result = await api_client.get_command(timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_sse_action_event_multiple_commands(self, api_client):
        """Test that multiple SSE action events are queued in order."""
        await api_client._handle_action_event({"id": "cmd-1", "type": "LIGHT"})
        await api_client._handle_action_event({"id": "cmd-2", "type": "FAN"})

        result1 = await api_client.get_command(timeout=1.0)
        result2 = await api_client.get_command(timeout=1.0)
        assert result1["id"] == "cmd-1"
        assert result2["id"] == "cmd-2"

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


class TestApiClientSSEEventHandling:
    """Tests for ApiClient SSE event handling."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager"),
            patch("app.api_client.config_store"),
        ):
            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            client._command_queue = asyncio.Queue()
            yield client

    @pytest.mark.asyncio
    async def test_config_event_calls_settings_callback(self, api_client):
        """Test that config SSE event calls settings callback."""
        callback = AsyncMock()
        api_client.register_settings_callback(callback)

        config_payload = {
            "configVersion": 1,
            "rdhMode": False,
            "status": "active",
            "light": {"day": {"on": "06:00", "off": "22:00"}},
            "climate": {"temperature": 25, "humidity": 60},
            "tank": {"ph": 6.5},
        }

        await api_client._handle_config_event(config_payload)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_action_event_queues_command(self, api_client):
        """Test that action SSE event puts command in queue."""
        action_payload = {"id": "action-1", "type": "LIGHT", "value": "on"}

        await api_client._handle_action_event(action_payload)

        result = await api_client.get_command(timeout=1.0)
        assert result is not None
        assert result["id"] == "action-1"

    @pytest.mark.asyncio
    async def test_action_event_without_id_ignored(self, api_client):
        """Test that action event without id is ignored."""
        action_payload = {"type": "UNKNOWN_TYPE", "value": "test"}

        await api_client._handle_action_event(action_payload)

        result = await api_client.get_command(timeout=0.1)
        assert result is None


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


class TestApiClientSSEListener:
    """Tests for SSE listener functionality."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
            patch("app.api_client.config_store") as mock_store,
        ):
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_store.get_config_version.return_value = 5

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            client._command_queue = asyncio.Queue()
            yield client

    @pytest.mark.asyncio
    async def test_start_sse_listener(self, api_client):
        """Test starting SSE listener."""
        with patch.object(api_client, "_sse_listener_loop", new_callable=AsyncMock):
            await api_client.start_sse_listener()

            assert api_client._sse_running is True
            assert api_client._sse_task is not None

            # Clean up
            api_client._sse_running = False
            if api_client._sse_task:
                api_client._sse_task.cancel()

    @pytest.mark.asyncio
    async def test_stop_cancels_sse_task(self, api_client):
        """Test that stop cancels SSE task."""
        # Start the listener
        with patch.object(api_client, "_sse_listener_loop", new_callable=AsyncMock):
            await api_client.start_sse_listener()

        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        api_client._client = mock_client

        # Stop should cancel the task
        await api_client.stop()

        assert api_client._sse_running is False
        assert api_client._sse_task is None

    @pytest.mark.asyncio
    async def test_sse_listener_loop_reconnects_on_error(self, api_client):
        """Test SSE listener reconnects on error with backoff."""
        call_count = 0
        api_client._sse_running = True

        async def mock_connect():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection failed")
            # Stop after 3 attempts
            api_client._sse_running = False

        with (
            patch.object(api_client, "_sse_connect", side_effect=mock_connect),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await api_client._sse_listener_loop()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_sse_listener_loop_handles_cancellation(self, api_client):
        """Test SSE listener handles cancellation gracefully."""

        async def mock_connect():
            raise asyncio.CancelledError()

        with patch.object(api_client, "_sse_connect", side_effect=mock_connect):
            await api_client._sse_listener_loop()

        # Should exit without error

    @pytest.mark.asyncio
    async def test_sse_connect_without_client_id(self, api_client):
        """Test SSE connect when no client ID available."""
        with (
            patch("app.api_client.auth_manager") as mock_auth,
            patch("asyncio.sleep", return_value=None),  # Mock any waits
        ):
            mock_auth.get_client_id.return_value = None

            await api_client._sse_connect()

        # Should return early without attempting connection

    @pytest.mark.asyncio
    async def test_sse_connect_non_200_status(self, api_client):
        """Test SSE connect handles non-200 status codes."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_sse_client = MagicMock()
        mock_sse_client.stream = MagicMock(return_value=mock_response)

        mock_client_context = MagicMock()
        mock_client_context.__aenter__ = AsyncMock(return_value=mock_sse_client)
        mock_client_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.api_client.auth_manager") as mock_auth,
            patch("httpx.AsyncClient", return_value=mock_client_context),
        ):
            mock_auth.get_client_id.return_value = "test-client-123"

            await api_client._sse_connect()

        # Should return early on non-200 status

    @pytest.mark.asyncio
    async def test_sse_event_parsing(self, api_client):
        """Test SSE event parsing by calling handle_sse_event directly."""
        mock_handler = AsyncMock()
        api_client._handle_sse_event = mock_handler

        # Simulate two events being parsed
        await api_client._handle_sse_event("config", '{"configVersion": 1}', None)
        await api_client._handle_sse_event("heartbeat", '{"configVersion": 1}', None)

        # Should have called handler for both events
        assert mock_handler.call_count == 2

    @pytest.mark.asyncio
    async def test_sse_multiline_data(self, api_client):
        """Test SSE parsing with multiline data by calling handle_sse_event."""
        # Simulate multiline data being joined
        multiline_data = '{"id": "123",\n"type": "LIGHT"}'

        with patch.object(
            api_client, "_handle_action_event", new_callable=AsyncMock
        ) as mock_action:
            await api_client._handle_sse_event("action", multiline_data, None)

            # Should have parsed the joined multiline data
            mock_action.assert_called_once()
            call_args = mock_action.call_args[0][0]
            assert call_args["type"] == "LIGHT"

    @pytest.mark.asyncio
    async def test_sse_event_id_parsing(self, api_client):
        """Test SSE event ID parsing."""
        # Test that event ID field can be parsed (though not currently used)
        with patch.object(api_client, "_handle_heartbeat_event", new_callable=AsyncMock):
            await api_client._handle_sse_event("heartbeat", '{"configVersion": 1}', "event-id-123")
            # Should parse without error

    @pytest.mark.asyncio
    async def test_handle_sse_event_invalid_json(self, api_client):
        """Test SSE event handler with invalid JSON."""
        # Should log error but not raise
        await api_client._handle_sse_event("config", "not valid json", None)

    @pytest.mark.asyncio
    async def test_handle_sse_event_unknown_type(self, api_client):
        """Test SSE event handler with unknown event type."""
        await api_client._handle_sse_event("unknown_type", '{"test": "data"}', None)
        # Should log debug message but not raise

    @pytest.mark.asyncio
    async def test_handle_connected_event(self, api_client):
        """Test handling connected SSE event."""
        payload = {"configVersion": 10}
        await api_client._handle_connected_event(payload)
        # Should log the version


class TestApiClientSSEEventHandlers:
    """Tests for specific SSE event handler methods."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager"),
            patch("app.api_client.config_store") as mock_store,
        ):
            mock_store.get_config_version.return_value = 5
            mock_store.save_full_config = MagicMock()

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            client._command_queue = asyncio.Queue()
            yield client

    @pytest.mark.asyncio
    async def test_handle_config_event(self, api_client):
        """Test handling config SSE event."""
        callback = AsyncMock()
        api_client.register_settings_callback(callback)

        config_payload = {
            "configVersion": 10,
            "rdhMode": True,
            "status": "active",
            "light": {"day": {"on": "06:00"}},
            "climate": {"temperature": 25},
            "tank": {"ph": 6.5},
        }

        await api_client._handle_config_event(config_payload)

        # Should call settings callback with extracted settings
        callback.assert_called_once()
        call_args = callback.call_args[0][0]
        assert call_args["rdh_mode"] is True
        assert call_args["status"] == "active"

    @pytest.mark.asyncio
    async def test_handle_config_event_callback_error(self, api_client):
        """Test config event handles callback errors gracefully."""
        callback = AsyncMock(side_effect=Exception("Callback failed"))
        api_client.register_settings_callback(callback)

        config_payload = {
            "configVersion": 10,
            "rdhMode": False,
            "status": "active",
            "light": {},
            "climate": {},
            "tank": {},
        }

        # Should not raise, just log error
        await api_client._handle_config_event(config_payload)

    @pytest.mark.asyncio
    async def test_handle_config_event_persists_device_assignments(self, api_client):
        """Config event with deviceAssignments forwards them to config_store."""
        assignments = [
            {"entityId": "gpio.relay1", "role": "WATER_PUMP", "slot": None},
            {
                "entityId": "esphome.scd30_temperature",
                "role": "TEMPERATURE_SENSOR",
                "slot": None,
            },
        ]
        config_payload = {
            "configVersion": 11,
            "rdhMode": False,
            "status": "active",
            "light": {},
            "climate": {},
            "tank": {},
            "deviceAssignments": assignments,
        }

        with patch("app.api_client.config_store") as mock_store:
            await api_client._handle_config_event(config_payload)

            mock_store.save_device_assignments.assert_called_once_with(assignments, 11)

    @pytest.mark.asyncio
    async def test_handle_config_event_missing_device_assignments(self, api_client):
        """Missing deviceAssignments → empty list passed to store."""
        config_payload = {
            "configVersion": 12,
            "rdhMode": False,
            "status": "active",
            "light": {},
            "climate": {},
            "tank": {},
        }

        with patch("app.api_client.config_store") as mock_store:
            await api_client._handle_config_event(config_payload)

            mock_store.save_device_assignments.assert_called_once_with([], 12)

    @pytest.mark.asyncio
    async def test_handle_config_event_invalid_device_assignments(self, api_client):
        """Non-list deviceAssignments coerced to empty list."""
        config_payload = {
            "configVersion": 13,
            "rdhMode": False,
            "status": "active",
            "light": {},
            "climate": {},
            "tank": {},
            "deviceAssignments": {"not": "a list"},
        }

        with patch("app.api_client.config_store") as mock_store:
            await api_client._handle_config_event(config_payload)

            mock_store.save_device_assignments.assert_called_once_with([], 13)

    @pytest.mark.asyncio
    async def test_handle_heartbeat_event_version_match(self, api_client):
        """Test heartbeat event when versions match."""
        with patch("app.api_client.config_store") as mock_store:
            mock_store.get_config_version.return_value = 10

            payload = {"configVersion": 10}
            await api_client._handle_heartbeat_event(payload)

        # Should log that versions match, no fetch needed

    @pytest.mark.asyncio
    async def test_handle_heartbeat_event_version_mismatch(self, api_client):
        """Test heartbeat event when versions don't match."""
        with patch("app.api_client.config_store") as mock_store:
            mock_store.get_config_version.return_value = 5

            mock_full_config = {
                "configVersion": 10,
                "rdhMode": False,
                "status": "active",
                "light": {},
                "climate": {},
                "tank": {},
            }

            with patch.object(api_client, "fetch_full_config", return_value=mock_full_config):
                callback = AsyncMock()
                api_client.register_settings_callback(callback)

                payload = {"configVersion": 10}
                await api_client._handle_heartbeat_event(payload)

                # Should fetch full config and call settings callback
                callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_heartbeat_event_fetch_fails(self, api_client):
        """Test heartbeat event when fetch_full_config returns None."""
        with patch("app.api_client.config_store") as mock_store:
            mock_store.get_config_version.return_value = 5

            with patch.object(api_client, "fetch_full_config", return_value=None):
                callback = AsyncMock()
                api_client.register_settings_callback(callback)

                payload = {"configVersion": 10}
                await api_client._handle_heartbeat_event(payload)

                # Should not call settings callback
                callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_heartbeat_event_callback_error(self, api_client):
        """Test heartbeat event handles callback errors gracefully."""
        with patch("app.api_client.config_store") as mock_store:
            mock_store.get_config_version.return_value = 5

            mock_full_config = {
                "configVersion": 10,
                "rdhMode": False,
                "status": "active",
                "light": {},
                "climate": {},
                "tank": {},
            }

            with patch.object(api_client, "fetch_full_config", return_value=mock_full_config):
                callback = AsyncMock(side_effect=Exception("Callback error"))
                api_client.register_settings_callback(callback)

                payload = {"configVersion": 10}
                # Should not raise
                await api_client._handle_heartbeat_event(payload)


class TestApiClientRetryLogic:
    """Tests for API client retry logic and exponential backoff."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance with fast retries."""
        # Configure very short retry times for testing
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None: {
                "api.url": "http://localhost:8080",
                "api.retry_max_attempts": 3,
                "api.retry_min_backoff": 0.001,  # 1ms
                "api.retry_max_backoff": 0.001,  # 1ms
                "api.batch_size": 100,
            }.get(key, default)
        )

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

    @pytest.mark.asyncio
    async def test_send_data_retries_on_timeout(self, api_client):
        """Test send_data retries on timeout errors."""
        mock_client = AsyncMock()
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise asyncio.TimeoutError()
            # Succeed on third attempt
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            return mock_response

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_send_data_retries_on_connect_error(self, api_client):
        """Test send_data retries on connection errors."""
        mock_client = AsyncMock()
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection failed")
            # Succeed on second attempt
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            return mock_response

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_send_data_fails_after_max_retries_timeout(self, api_client):
        """Test send_data fails after max retries with timeout."""
        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            raise asyncio.TimeoutError()

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        assert "timed out" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_fails_after_max_retries_connect_error(self, api_client):
        """Test send_data fails after max retries with connection error."""
        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            raise httpx.ConnectError("Connection failed")

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        assert "offline" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_http_status_error(self, api_client):
        """Test send_data handles HTTP status errors."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        async def mock_post(*args, **kwargs):
            raise httpx.HTTPStatusError("HTTP Error", request=MagicMock(), response=mock_response)

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        assert "http error" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_request_error(self, api_client):
        """Test send_data handles generic request errors."""
        from tenacity import RetryError

        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            raise httpx.RequestError("Request failed")

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        # The error message will be either "request error" or "api unavailable" depending on retry behavior
        assert any(
            term in message.lower()
            for term in ["request error", "api unavailable", "request failed"]
        )

    @pytest.mark.asyncio
    async def test_send_data_unexpected_error(self, api_client):
        """Test send_data handles unexpected errors."""
        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            raise ValueError("Unexpected error")

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        assert "unexpected error" in message.lower()


class TestApiClientSendCommandResult:
    """Tests for send_command_result method."""

    @pytest.fixture
    def api_client(self, mock_config):
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
            yield client

    @pytest.mark.asyncio
    async def test_send_command_result_success(self, api_client):
        """Test successful command result transmission."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        api_client._client = mock_client

        result = await api_client.send_command_result("cmd-123", True, "Success")

        assert result is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "cmd-123" in call_args[0][0]
        assert call_args[1]["json"]["success"] is True
        assert call_args[1]["json"]["message"] == "Success"

    @pytest.mark.asyncio
    async def test_send_command_result_failure_message(self, api_client):
        """Test sending command result with failure."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        api_client._client = mock_client

        result = await api_client.send_command_result("cmd-123", False, "Failed to execute")

        assert result is True
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["success"] is False
        assert call_args[1]["json"]["message"] == "Failed to execute"

    @pytest.mark.asyncio
    async def test_send_command_result_not_started(self, api_client):
        """Test send_command_result when client not started."""
        api_client._client = None

        result = await api_client.send_command_result("cmd-123", True, "Success")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_command_result_not_authenticated(self, api_client):
        """Test send_command_result when not authenticated."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = False

            result = await api_client.send_command_result("cmd-123", True, "Success")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_command_result_no_client_id(self, api_client):
        """Test send_command_result when no client ID available."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = None

            result = await api_client.send_command_result("cmd-123", True, "Success")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_command_result_error(self, api_client):
        """Test send_command_result handles errors."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        api_client._client = mock_client

        result = await api_client.send_command_result("cmd-123", True, "Success")

        assert result is False


class TestApiClientFetchFullConfig:
    """Tests for fetch_full_config method."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance."""
        with (
            patch("app.api_client.config", mock_config),
            patch("app.api_client.auth_manager") as mock_auth,
            patch("app.api_client.config_store") as mock_store,
        ):
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_store.save_full_config = MagicMock()

            from app.api_client import ApiClient
            from app.utils.singleton import SingletonMeta

            if ApiClient in SingletonMeta._instances:
                del SingletonMeta._instances[ApiClient]

            client = ApiClient()
            yield client

    @pytest.mark.asyncio
    async def test_fetch_full_config_success(self, api_client):
        """Test successful full config fetch."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "configVersion": 15,
            "rdhMode": False,
            "status": "active",
        }
        mock_client.get.return_value = mock_response
        api_client._client = mock_client

        result = await api_client.fetch_full_config()

        assert result is not None
        assert result["configVersion"] == 15

    @pytest.mark.asyncio
    async def test_fetch_full_config_204_no_space(self, api_client):
        """Test fetch_full_config when bridge connected but no space created."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.get.return_value = mock_response
        api_client._client = mock_client

        result = await api_client.fetch_full_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_full_config_unexpected_status(self, api_client):
        """Test fetch_full_config with unexpected status code."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response
        api_client._client = mock_client

        result = await api_client.fetch_full_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_full_config_not_started(self, api_client):
        """Test fetch_full_config when client not started."""
        api_client._client = None

        result = await api_client.fetch_full_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_full_config_not_authenticated(self, api_client):
        """Test fetch_full_config when not authenticated."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = False

            result = await api_client.fetch_full_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_full_config_no_client_id(self, api_client):
        """Test fetch_full_config when no client ID available."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = None

            result = await api_client.fetch_full_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_full_config_error(self, api_client):
        """Test fetch_full_config handles errors."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Network error")
        api_client._client = mock_client

        result = await api_client.fetch_full_config()

        assert result is None


class TestApiClientDataValidation:
    """Tests for data validation and preconditions."""

    @pytest.fixture
    def api_client(self, mock_config):
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
            yield client

    def test_validate_send_preconditions_success(self, api_client):
        """Test successful validation of send preconditions."""
        api_client._client = AsyncMock()

        success, message, client_id = api_client._validate_send_preconditions()

        assert success is True
        assert message == ""
        assert client_id == "test-client-123"

    def test_validate_send_preconditions_no_client(self, api_client):
        """Test validation fails when client not started."""
        api_client._client = None

        success, message, client_id = api_client._validate_send_preconditions()

        assert success is False
        assert "not started" in message.lower()
        assert client_id is None

    def test_validate_send_preconditions_not_authenticated(self, api_client):
        """Test validation fails when not authenticated."""
        api_client._client = AsyncMock()

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = False

            success, message, client_id = api_client._validate_send_preconditions()

        assert success is False
        assert "authenticated" in message.lower()
        assert client_id is None

    def test_validate_send_preconditions_no_client_id(self, api_client):
        """Test validation fails when no client ID."""
        api_client._client = AsyncMock()

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = None

            success, message, client_id = api_client._validate_send_preconditions()

        assert success is False
        assert "client id" in message.lower()
        assert client_id is None

    @pytest.mark.asyncio
    async def test_send_data_not_ready_connected(self, api_client):
        """Test send_data when not ready but connected."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_auth.is_ready_for_data.return_value = False
            mock_auth.check_connection_status = AsyncMock(return_value=(True, "connected"))

            api_client.add_data_log(LogType.TEMPERATURE, 25.5)
            success, message = await api_client.send_data()

        assert success is False
        assert "space not created" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_not_ready_not_connected(self, api_client):
        """Test send_data when not ready and not connected."""
        mock_client = AsyncMock()
        api_client._client = mock_client

        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"
            mock_auth.is_ready_for_data.return_value = False
            mock_auth.check_connection_status = AsyncMock(return_value=(False, "disconnected"))

            api_client.add_data_log(LogType.TEMPERATURE, 25.5)
            success, message = await api_client.send_data()

        assert success is False
        assert "not connected" in message.lower()


class TestApiClientLogging:
    """Tests for API value logging functionality."""

    @pytest.fixture
    def api_client(self, mock_config, tmp_path):
        """Create a configured ApiClient instance with logging enabled."""
        log_config = mock_config.config.copy()
        log_config["api"]["log_values"] = True
        log_config["general"]["log_dir"] = str(tmp_path)

        mock_config.get.side_effect = lambda key, default=None: {
            "api.log_values": True,
            "general.log_dir": str(tmp_path),
            "api.url": "http://localhost:8080",
            "api.timeout": 30,
            "api.verify_ssl": True,
            "general.log_level": "INFO",
        }.get(key, default)

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

    @pytest.mark.asyncio
    async def test_start_creates_log_directory(self, api_client, tmp_path):
        """Test that start creates log directory when logging enabled."""
        await api_client.start()

        expected_dir = tmp_path / "api_values"
        assert expected_dir.exists()

        await api_client.stop()

    @pytest.mark.asyncio
    async def test_log_transmission_results_success(self, api_client, tmp_path):
        """Test logging transmission results on success."""
        # Ensure the api_values directory exists
        api_values_dir = tmp_path / "api_values"
        api_values_dir.mkdir(parents=True, exist_ok=True)
        api_client._api_log_dir = str(api_values_dir)

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, _ = await api_client.send_data()

        assert success is True
        # Log files should be created in the tmp directory

    @pytest.mark.asyncio
    async def test_log_transmission_results_failure(self, api_client, mock_config):
        """Test logging transmission results on failure."""
        # Configure very short retry times
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None: {
                "api.retry_max_attempts": 1,  # Only 1 attempt for speed
                "api.retry_min_backoff": 0.001,
                "api.retry_max_backoff": 0.001,
            }.get(key, default)
        )

        with patch("app.api_client.config", mock_config):
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=MagicMock(status_code=500, text="Error")
            )
            api_client._client = mock_client

            api_client.add_data_log(LogType.TEMPERATURE, 25.5)
            success, _ = await api_client.send_data()

            assert success is False
            # Error should be logged


class TestApiClientStateManagement:
    """Tests for API client state management."""

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

    def test_get_init_state_not_started(self, api_client):
        """Test get_init_state when not started."""
        state = api_client.get_init_state()

        assert state["initialized"] is False
        assert state["has_command_queue"] is False
        assert state["sse_running"] is False

    @pytest.mark.asyncio
    async def test_get_init_state_started(self, api_client):
        """Test get_init_state when started."""
        await api_client.start()

        state = api_client.get_init_state()

        assert state["initialized"] is True
        assert state["has_command_queue"] is True
        assert state["sse_running"] is False

        await api_client.stop()

    @pytest.mark.asyncio
    async def test_get_init_state_with_sse(self, api_client):
        """Test get_init_state when SSE listener is running."""
        await api_client.start()

        with patch.object(api_client, "_sse_listener_loop", new_callable=AsyncMock):
            await api_client.start_sse_listener()

            state = api_client.get_init_state()

            assert state["sse_running"] is True

            api_client._sse_running = False
            if api_client._sse_task:
                api_client._sse_task.cancel()

        await api_client.stop()

    @pytest.mark.asyncio
    async def test_get_command_without_timeout(self, api_client):
        """Test get_command without timeout."""
        api_client._command_queue = asyncio.Queue()
        command = {"id": "test-cmd", "type": "LIGHT"}
        await api_client._command_queue.put(command)

        result = await api_client.get_command()

        assert result == command


class TestApiClientProblemDetectionEdgeCases:
    """Tests for edge cases in problem detection."""

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

    def test_detect_problems_none_value(self, api_client):
        """Test problem detection with None value."""
        data_points = [{"type": "TEMPERATURE", "value": None, "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        # Should skip None values
        assert len(api_client._problems) == 0

    def test_detect_problems_missing_type(self, api_client):
        """Test problem detection with missing type."""
        data_points = [{"value": 25, "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        # Should skip missing type
        assert len(api_client._problems) == 0

    def test_detect_problems_ph_low(self, api_client):
        """Test detection of pH too low."""
        data_points = [{"type": "PH", "value": -1, "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 1
        assert api_client._problems[0]["type"] == "PH"

    def test_detect_problems_ph_high(self, api_client):
        """Test detection of pH too high."""
        data_points = [{"type": "PH", "value": 15, "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 1
        assert api_client._problems[0]["type"] == "PH"

    def test_detect_problems_non_numeric_value(self, api_client):
        """Test problem detection with non-numeric value for numeric type."""
        data_points = [{"type": "TEMPERATURE", "value": "not a number", "integration": "test"}]

        api_client._detect_problems_from_data(data_points)

        # Should not raise, should skip
        assert len(api_client._problems) == 0

    def test_detect_problems_various_failure_values(self, api_client):
        """Test detection of various failure values."""
        data_points = [
            {"type": "TEMPERATURE", "value": "error", "integration": "test"},
            {"type": "HUMIDITY", "value": "failed", "integration": "test"},
            {"type": "PH", "value": "null", "integration": "test"},
            {"type": "TANK_ML", "value": "unavailable", "integration": "test"},
        ]

        api_client._detect_problems_from_data(data_points)

        assert len(api_client._problems) == 4
        for problem in api_client._problems:
            assert problem["status"] == "CONNECTION"


class TestApiClientEdgeCases:
    """Tests for additional edge cases and error scenarios."""

    @pytest.fixture
    def api_client(self, mock_config):
        """Create a configured ApiClient instance with fast retries."""
        # Configure very short retry times for testing
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None: {
                "api.url": "http://localhost:8080",
                "api.retry_max_attempts": 3,
                "api.retry_min_backoff": 0.001,  # 1ms
                "api.retry_max_backoff": 0.001,  # 1ms
                "api.batch_size": 100,
            }.get(key, default)
        )

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

    @pytest.mark.asyncio
    async def test_send_data_connect_timeout_after_retries(self, api_client):
        """Test send_data handles ConnectTimeout specifically."""
        from tenacity import Future, RetryError

        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            raise httpx.ConnectTimeout("Connection timed out")

        mock_client.post = mock_post
        api_client._client = mock_client

        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        success, message = await api_client.send_data()

        assert success is False
        assert "offline" in message.lower() or "connection" in message.lower()

    @pytest.mark.asyncio
    async def test_send_data_with_empty_data_logs(self, api_client):
        """Test send_data with no data logs."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        api_client._client = mock_client

        # Don't add any data logs
        success, message = await api_client.send_data()

        assert success is True
        # Should still send (with empty array)

    @pytest.mark.asyncio
    async def test_clear_sent_data_returns_counts(self, api_client):
        """Test that _clear_sent_data returns correct counts."""
        api_client.add_data_log(LogType.TEMPERATURE, 25.5)
        api_client.add_data_log(LogType.HUMIDITY, 60.0)
        api_client.add_problem(ProblemType.TEMPERATURE, ProblemStatus.RANGE, "Test")
        api_client.acknowledge_action("action-1", True, False)

        logs, problems, actions = api_client._clear_sent_data()

        assert logs == 2
        assert problems == 1
        assert actions == 1
        assert len(api_client._data_logs) == 0
        assert len(api_client._problems) == 0
        assert len(api_client._actions) == 0

    def test_get_headers_with_client_id(self, api_client):
        """Test _get_headers includes client ID when authenticated."""
        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = True
            mock_auth.get_client_id.return_value = "test-client-123"

            headers = api_client._get_headers()

            assert "X-Client-ID" in headers
            assert headers["X-Client-ID"] == "test-client-123"

    def test_get_headers_without_client_id(self, api_client):
        """Test _get_headers when not authenticated."""
        with patch("app.api_client.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = False

            headers = api_client._get_headers()

            # Should still return headers, but without client ID
            assert isinstance(headers, dict)

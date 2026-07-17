"""
Tests for Main Application Module.

This module tests the Application class lifecycle, async tasks,
and integration management.
"""

import asyncio
import signal
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import Application


@pytest.fixture
def reset_application_singleton():
    """Reset the Application singleton before and after each test."""
    Application._instance = None
    Application._lock = threading.Lock()
    yield
    Application._instance = None
    Application._lock = threading.Lock()


@pytest.fixture
def mock_dependencies():
    """Mock all Application dependencies."""
    with (
        patch("app.main.auth_manager") as mock_auth,
        patch("app.main.mqtt_transport") as mock_api,
        patch("app.main.queue_manager") as mock_queue,
        patch("app.main.registry") as mock_registry,
        patch("app.main.config") as mock_config,
        patch("app.main.config_store") as mock_config_store,
        patch("app.main.discover_integrations") as mock_discover,
        patch("app.main.get_integration_class_by_config_key") as mock_get_class,
    ):
        # Configure auth_manager
        mock_auth.start = AsyncMock()
        mock_auth.stop = AsyncMock()
        mock_auth.is_authenticated = MagicMock(return_value=True)
        mock_auth.is_ready_for_data = MagicMock(return_value=True)
        mock_auth.pair_with_code = AsyncMock(return_value=True)
        mock_auth.refresh_token = AsyncMock(return_value=True)

        # Configure mqtt_transport
        mock_api.start = AsyncMock()
        mock_api.stop = AsyncMock()
        mock_api.register_settings_callback = MagicMock()
        mock_api.send_data = AsyncMock(return_value=(True, "Success"))
        mock_api.send_manifest = AsyncMock(return_value=(True, "Manifest v1 published"))
        mock_api.get_command = AsyncMock(return_value=None)
        mock_api.send_command_result = AsyncMock()
        mock_api.is_connected = MagicMock(return_value=True)

        # Configure queue_manager
        mock_queue.start = AsyncMock()
        mock_queue.stop = AsyncMock()
        mock_queue.put = AsyncMock()
        mock_queue.get_data_points = AsyncMock(return_value=[])
        mock_queue.mark_processed = AsyncMock()
        mock_queue.requeue_data_points = AsyncMock()

        # Configure config_store
        mock_config_store.start = MagicMock()
        mock_config_store.stop = MagicMock()
        mock_config_store.get_full_config = MagicMock(return_value=(None, 0))

        # Configure registry
        mock_registry.get_sensor_integration = MagicMock(return_value=None)
        mock_registry.get_actuator_integration = MagicMock(return_value=None)

        # Configure config with short intervals for testing
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None: {
                "web.enabled": False,
                "api.connection_timeout": 300,
                "api.space_creation_timeout": 1800,
                "general.collection_interval": 0.01,  # Very short for testing
                "api.batch_size": 100,
                "api.transmission_interval": 0.01,  # Very short for testing
            }.get(key, default)
        )
        mock_config.get_section = MagicMock(return_value={})

        # Configure integration discovery
        mock_discover.return_value = []
        mock_get_class.return_value = None

        yield {
            "auth_manager": mock_auth,
            "mqtt_transport": mock_api,
            "queue_manager": mock_queue,
            "registry": mock_registry,
            "config": mock_config,
            "config_store": mock_config_store,
            "discover_integrations": mock_discover,
            "get_integration_class_by_config_key": mock_get_class,
        }


@pytest.fixture
def mock_integration():
    """Create a mock integration instance."""
    integration = AsyncMock()
    integration.name = "test_integration"
    integration.connect = AsyncMock(return_value=True)
    integration.disconnect = AsyncMock()
    integration.execute_command = AsyncMock(return_value=True)
    integration.apply_settings = AsyncMock()
    integration.register_capabilities = MagicMock()

    async def receive_data_generator():
        yield {"type": "temperature", "value": 25.5}

    integration.receive_data = MagicMock(return_value=receive_data_generator())
    return integration


class TestApplicationSingleton:
    """Tests for Application singleton pattern."""

    def test_singleton_returns_same_instance(self, reset_application_singleton):
        """Test that Application returns the same instance."""
        with patch("app.main.signal.signal"):
            app1 = Application()
            app2 = Application()

            assert app1 is app2

    def test_singleton_thread_safety(self, reset_application_singleton):
        """Test that singleton is thread-safe."""
        instances = []
        errors = []

        def create_instance():
            try:
                with patch("app.main.signal.signal"):
                    instance = Application()
                    instances.append(instance)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(i is instances[0] for i in instances)


class TestApplicationInit:
    """Tests for Application initialization."""

    def test_init_sets_default_values(self, reset_application_singleton):
        """Test that initialization sets correct default values."""
        with patch("app.main.signal.signal"):
            app = Application()

            assert app._integrations == {}
            assert app._running is False
            assert app._tasks == set()
            assert app._initialized is True
            assert app.loop is None

    @patch("app.main.threading.current_thread")
    @patch("app.main.threading.main_thread")
    @patch("app.main.signal.signal")
    def test_init_registers_signal_handlers_in_main_thread(
        self, mock_signal, mock_main_thread, mock_current_thread, reset_application_singleton
    ):
        """Test signal handlers are registered in main thread."""
        mock_thread = MagicMock()
        mock_current_thread.return_value = mock_thread
        mock_main_thread.return_value = mock_thread

        app = Application()

        assert mock_signal.call_count == 2
        mock_signal.assert_any_call(signal.SIGINT, app._signal_handler)
        mock_signal.assert_any_call(signal.SIGTERM, app._signal_handler)

    @patch("app.main.threading.current_thread")
    @patch("app.main.threading.main_thread")
    @patch("app.main.signal.signal")
    def test_init_skips_signal_handlers_in_non_main_thread(
        self, mock_signal, mock_main_thread, mock_current_thread, reset_application_singleton
    ):
        """Test signal handlers are not registered in non-main thread."""
        mock_current_thread.return_value = MagicMock()
        mock_main_thread.return_value = MagicMock()  # Different object

        Application()

        mock_signal.assert_not_called()


class TestApplicationStart:
    """Tests for Application.start()."""

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self, reset_application_singleton, mock_dependencies):
        """Test that start sets the running flag."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            assert app._running is True
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_prevents_double_start(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that calling start twice doesn't double-start."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()
            await app.start()  # Second call should be ignored

            mock_dependencies["auth_manager"].start.assert_called_once()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_calls_auth_manager_start(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that start calls auth_manager.start()."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            mock_dependencies["auth_manager"].start.assert_called_once()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_starts_web_server_when_enabled(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that web server is started when enabled."""
        mock_dependencies["config"].get = MagicMock(
            side_effect=lambda key, default=None: {
                "web.enabled": True,
            }.get(key, default)
        )

        with patch("app.main.signal.signal"), patch("app.main.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            app = Application()

            # Patch _start_web_server to avoid web.app import issues
            with patch.object(app, "_start_web_server") as mock_start_web:
                await app.start()
                mock_start_web.assert_called_once()

            await app.stop()

    @pytest.mark.asyncio
    async def test_start_skips_web_server_when_disabled(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that web server is not started when disabled."""
        mock_dependencies["config"].get = MagicMock(
            side_effect=lambda key, default=None: {
                "web.enabled": False,
            }.get(key, default)
        )

        with patch("app.main.signal.signal"), patch("app.main.threading.Thread") as mock_thread:
            app = Application()
            await app.start()

            mock_thread.assert_not_called()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_calls_handle_authentication(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that start calls _handle_authentication."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            # Verify auth_manager methods were called through _handle_authentication
            mock_dependencies["auth_manager"].is_authenticated.assert_called()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_starts_queue_manager(self, reset_application_singleton, mock_dependencies):
        """Test that start calls queue_manager.start()."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            mock_dependencies["queue_manager"].start.assert_called_once()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_loads_integrations(self, reset_application_singleton, mock_dependencies):
        """Test that start loads integrations."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            mock_dependencies["discover_integrations"].assert_called_once()
            await app.stop()

    @pytest.mark.asyncio
    async def test_start_creates_tasks(self, reset_application_singleton, mock_dependencies):
        """Test that start creates background tasks."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            assert len(app._tasks) == 3  # data_collection, data_transmission, command_execution
            await app.stop()


class TestApplicationStop:
    """Tests for Application.stop()."""

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self, reset_application_singleton, mock_dependencies):
        """Test that stop clears the running flag."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()
            await app.stop()

            assert app._running is False

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, reset_application_singleton, mock_dependencies):
        """Test stop when application is not running."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.stop()  # Should not raise

            assert app._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, reset_application_singleton, mock_dependencies):
        """Test that stop cancels all tasks."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()

            # Tasks should exist
            assert len(app._tasks) > 0

            await app.stop()

            # Tasks should be cleared
            assert len(app._tasks) == 0

    @pytest.mark.asyncio
    async def test_stop_disconnects_integrations(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that stop disconnects all integrations."""
        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test": mock_integration}
            app._running = True

            await app.stop()

            mock_integration.disconnect.assert_called_once()
            assert len(app._integrations) == 0

    @pytest.mark.asyncio
    async def test_stop_stops_transport(self, reset_application_singleton, mock_dependencies):
        """Test that stop calls mqtt_transport.stop()."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()
            await app.stop()

            mock_dependencies["mqtt_transport"].stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_stops_auth_manager(self, reset_application_singleton, mock_dependencies):
        """Test that stop calls auth_manager.stop()."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()
            await app.stop()

            mock_dependencies["auth_manager"].stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_stops_queue_manager(self, reset_application_singleton, mock_dependencies):
        """Test that stop calls queue_manager.stop()."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app.start()
            await app.stop()

            mock_dependencies["queue_manager"].stop.assert_called_once()


class TestHandleAuthentication:
    """Tests for Application._handle_authentication() (non-blocking pairing)."""

    @pytest.mark.asyncio
    async def test_already_paired_returns(self, reset_application_singleton, mock_dependencies):
        """When already paired it returns immediately without blocking."""
        mock_dependencies["auth_manager"].is_authenticated.return_value = True

        with patch("app.main.signal.signal"):
            app = Application()
            await app._handle_authentication()

            mock_dependencies["auth_manager"].is_authenticated.assert_called()

    @pytest.mark.asyncio
    async def test_unpaired_does_not_block_or_exit(
        self, reset_application_singleton, mock_dependencies
    ):
        """When unpaired it logs and returns without blocking or exiting."""
        mock_dependencies["auth_manager"].is_authenticated.return_value = False

        with patch("app.main.signal.signal"), patch("sys.exit") as mock_exit:
            app = Application()
            # Must complete (not block) and must not exit.
            await app._handle_authentication()

            mock_exit.assert_not_called()


class TestLoadIntegrations:
    """Tests for Application._load_integrations()."""

    @pytest.mark.asyncio
    async def test_load_disabled_integration_skipped(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that disabled integrations are skipped."""
        mock_dependencies["config"].get_section.return_value = {"test": {"enabled": False}}

        with patch("app.main.signal.signal"):
            app = Application()
            await app._load_integrations()

            assert len(app._integrations) == 0

    @pytest.mark.asyncio
    async def test_load_integration_not_found(self, reset_application_singleton, mock_dependencies):
        """Test handling when integration class is not found."""
        mock_dependencies["config"].get_section.return_value = {"test": {"enabled": True}}
        mock_dependencies["get_integration_class_by_config_key"].return_value = None

        with (
            patch("app.main.signal.signal"),
            patch("app.main.get_integration_class", return_value=None),
        ):
            app = Application()
            await app._load_integrations()

            assert len(app._integrations) == 0

    @pytest.mark.asyncio
    async def test_load_integration_connect_failure(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test handling when integration connect fails."""
        mock_dependencies["config"].get_section.return_value = {"test": {"enabled": True}}
        mock_integration.connect.return_value = False
        mock_class = MagicMock(return_value=mock_integration)
        mock_dependencies["get_integration_class_by_config_key"].return_value = mock_class

        with patch("app.main.signal.signal"):
            app = Application()
            await app._load_integrations()

            assert len(app._integrations) == 0

    @pytest.mark.asyncio
    async def test_load_integration_success(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test successful integration loading."""
        mock_dependencies["config"].get_section.return_value = {"test": {"enabled": True}}
        mock_class = MagicMock(return_value=mock_integration)
        mock_dependencies["get_integration_class_by_config_key"].return_value = mock_class

        with patch("app.main.signal.signal"):
            app = Application()
            await app._load_integrations()

            assert mock_integration.name in app._integrations

    @pytest.mark.asyncio
    async def test_register_capabilities_called(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that register_capabilities is called on loaded integration."""
        mock_dependencies["config"].get_section.return_value = {"test": {"enabled": True}}
        mock_class = MagicMock(return_value=mock_integration)
        mock_dependencies["get_integration_class_by_config_key"].return_value = mock_class

        with patch("app.main.signal.signal"):
            app = Application()
            await app._load_integrations()

            mock_integration.register_capabilities.assert_called_once_with(
                mock_dependencies["registry"]
            )


class TestDataCollectionTask:
    """Tests for Application._data_collection_task()."""

    @pytest.mark.asyncio
    async def test_collects_data_from_integrations(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that data is collected from integrations."""
        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True
            app._integrations = {"test": mock_integration}

            # Run one iteration then stop
            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_collection_task()

    @pytest.mark.asyncio
    async def test_adds_timestamp_and_integration_name(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that timestamp and integration name are added to data."""
        mock_integration = AsyncMock()
        mock_integration.name = "test_integration"

        async def data_generator():
            yield {"type": "temperature", "value": 25.5}

        mock_integration.receive_data.return_value = data_generator()

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True
            app._integrations = {"test": mock_integration}

            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_collection_task()

            # Verify put was called with enriched data
            if mock_dependencies["queue_manager"].put.called:
                call_args = mock_dependencies["queue_manager"].put.call_args[0][0]
                assert "timestamp" in call_args
                assert "integration" in call_args

    @pytest.mark.asyncio
    async def test_handles_integration_errors(self, reset_application_singleton, mock_dependencies):
        """Test that integration errors are handled gracefully."""
        mock_integration = AsyncMock()
        mock_integration.name = "test_integration"

        async def failing_generator():
            raise Exception("Integration error")
            yield  # Makes this an async generator

        mock_integration.receive_data.return_value = failing_generator()

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True
            app._integrations = {"test": mock_integration}

            async def stop_after_one():
                await asyncio.sleep(0.05)
                app._running = False

            asyncio.create_task(stop_after_one())
            # Should not raise
            await app._data_collection_task()

    @pytest.mark.asyncio
    async def test_cancellation_handled(self, reset_application_singleton, mock_dependencies):
        """Test that task cancellation is handled gracefully."""
        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True
            app._integrations = {}

            task = asyncio.create_task(app._data_collection_task())
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected


class TestDataTransmissionTask:
    """Tests for Application._data_transmission_task()."""

    @pytest.mark.asyncio
    async def test_sends_data_when_ready(self, reset_application_singleton, mock_dependencies):
        """Test that data is sent when client is ready."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = True
        mock_dependencies["queue_manager"].get_data_points.return_value = [
            {"type": "temperature", "value": 25.5}
        ]

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_transmission_task()

            mock_dependencies["mqtt_transport"].send_data.assert_called()

    @pytest.mark.asyncio
    async def test_waits_when_not_ready_for_data(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that transmission waits when not ready for data."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = False

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_transmission_task()

            # send_data should not be called
            mock_dependencies["mqtt_transport"].send_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_processed_on_success(self, reset_application_singleton, mock_dependencies):
        """Test that data is marked processed on successful send."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = True
        data_points = [{"type": "temperature", "value": 25.5}]
        mock_dependencies["queue_manager"].get_data_points.return_value = data_points
        mock_dependencies["mqtt_transport"].send_data.return_value = (True, "Success")

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_transmission_task()

            mock_dependencies["queue_manager"].mark_processed.assert_called_with(data_points)

    @pytest.mark.asyncio
    async def test_requeues_on_failure(self, reset_application_singleton, mock_dependencies):
        """Test that data is requeued on send failure."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = True
        data_points = [{"type": "temperature", "value": 25.5}]
        mock_dependencies["queue_manager"].get_data_points.return_value = data_points
        mock_dependencies["mqtt_transport"].send_data.return_value = (False, "Error")

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            async def stop_after_one():
                await asyncio.sleep(0.1)
                app._running = False

            asyncio.create_task(stop_after_one())
            await app._data_transmission_task()

            mock_dependencies["queue_manager"].requeue_data_points.assert_called_with(data_points)

    @pytest.mark.asyncio
    async def test_cancellation_handled(self, reset_application_singleton, mock_dependencies):
        """Test that task cancellation is handled gracefully."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = True
        mock_dependencies["queue_manager"].get_data_points.return_value = []

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            task = asyncio.create_task(app._data_transmission_task())
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected


class TestCommandExecutionTask:
    """Tests for Application._command_execution_task()."""

    @pytest.mark.asyncio
    async def test_processes_commands_when_ready(
        self, reset_application_singleton, mock_dependencies
    ):
        """Test that commands are processed when client is ready."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = True

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            # Track calls and stop after first
            call_count = [0]

            async def get_command_and_stop(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] >= 1:
                    app._running = False
                return None

            with patch.object(
                mock_dependencies["mqtt_transport"],
                "get_command",
                AsyncMock(side_effect=get_command_and_stop),
            ):
                await app._command_execution_task()

            assert call_count[0] >= 1

    @pytest.mark.asyncio
    async def test_waits_when_not_ready(self, reset_application_singleton, mock_dependencies):
        """Test that command processing waits when not ready."""
        mock_dependencies["auth_manager"].is_ready_for_data.return_value = False

        with patch("app.main.signal.signal"):
            app = Application()
            app._running = True

            # Create a task and cancel it after brief time
            task = asyncio.create_task(app._command_execution_task())
            await asyncio.sleep(0.02)
            app._running = False
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

            # get_command should not be called when not ready
            mock_dependencies["mqtt_transport"].get_command.assert_not_called()


class TestProcessCommand:
    """Tests for Application._process_command()."""

    @pytest.mark.asyncio
    async def test_missing_command_id(self, reset_application_singleton, mock_dependencies):
        """Test handling command without ID."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app._process_command({"action": "test"})

            # send_command_result should not be called without ID
            mock_dependencies["mqtt_transport"].send_command_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_required_fields(self, reset_application_singleton, mock_dependencies):
        """Test handling command without required fields."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app._process_command({"id": "cmd-1"})

            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", False, "Missing required fields"
            )

    @pytest.mark.asyncio
    async def test_unknown_target_type(self, reset_application_singleton, mock_dependencies):
        """Test handling command with unknown target type."""
        with patch("app.main.signal.signal"):
            app = Application()
            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "unknown",
                    "targetId": "target-1",
                    "action": "test",
                }
            )

            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", False, "Unknown target type: unknown"
            )

    @pytest.mark.asyncio
    async def test_integration_not_found(self, reset_application_singleton, mock_dependencies):
        """Test handling command when integration not found."""
        mock_dependencies["registry"].get_sensor_integration.return_value = None

        with patch("app.main.signal.signal"):
            app = Application()
            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "sensor",
                    "targetId": "sensor-1",
                    "action": "read",
                }
            )

            mock_dependencies["mqtt_transport"].send_command_result.assert_called()
            call_args = mock_dependencies["mqtt_transport"].send_command_result.call_args
            assert call_args[0][1] is False  # success = False

    @pytest.mark.asyncio
    async def test_sensor_command_routing(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that sensor commands are routed correctly."""
        mock_dependencies["registry"].get_sensor_integration.return_value = "test_integration"

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "sensor",
                    "targetId": "sensor-1",
                    "action": "read",
                }
            )

            mock_integration.execute_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_actuator_command_routing(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that actuator commands are routed correctly."""
        mock_dependencies["registry"].get_actuator_integration.return_value = "test_integration"

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "actuator",
                    "targetId": "pump-1",
                    "action": "on",
                }
            )

            mock_integration.execute_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_execution_success(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test successful command execution."""
        mock_dependencies["registry"].get_sensor_integration.return_value = "test_integration"
        mock_integration.execute_command.return_value = True

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "sensor",
                    "targetId": "sensor-1",
                    "action": "read",
                }
            )

            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", True, "Command executed successfully"
            )

    @pytest.mark.asyncio
    async def test_command_execution_failure(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test command execution failure."""
        mock_dependencies["registry"].get_sensor_integration.return_value = "test_integration"
        mock_integration.execute_command.return_value = False

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "sensor",
                    "targetId": "sensor-1",
                    "action": "read",
                }
            )

            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", False, "Command execution failed"
            )

    @pytest.mark.asyncio
    async def test_entity_id_target_resolves_via_registry(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """A dotted targetId (`<domain>.<name>`, §16.1) resolves through
        registry.get_device to the owning integration, and the integration
        receives the LOCAL device name — not the dotted id."""
        device = MagicMock()
        device.integration_name = "test_integration"
        device.name = "pump-1"
        mock_dependencies["registry"].get_device.return_value = device
        mock_integration.execute_command.return_value = True

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "actuator",
                    "targetId": "gpio.pump-1",
                    "action": "on",
                }
            )

            mock_dependencies["registry"].get_device.assert_called_once_with("gpio.pump-1")
            # The legacy name-indexed lookups are bypassed entirely.
            mock_dependencies["registry"].get_actuator_integration.assert_not_called()
            mock_integration.execute_command.assert_called_once_with("pump-1", "on", {})
            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", True, "Command executed successfully"
            )

    @pytest.mark.asyncio
    async def test_entity_id_target_unknown_entity(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """An unknown dotted targetId is acked success=false."""
        mock_dependencies["registry"].get_device.return_value = None

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "actuator",
                    "targetId": "gpio.nope",
                    "action": "on",
                }
            )

            mock_integration.execute_command.assert_not_called()
            mock_dependencies["mqtt_transport"].send_command_result.assert_called_with(
                "cmd-1", False, "Unknown entity: gpio.nope"
            )

    @pytest.mark.asyncio
    async def test_bare_name_target_still_routes(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Backward compatibility: a bare (dotless) targetId still resolves
        via the legacy name-indexed actuator lookup."""
        mock_dependencies["registry"].get_actuator_integration.return_value = "test_integration"
        mock_integration.execute_command.return_value = True

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "actuator",
                    "targetId": "pump-1",
                    "action": "on",
                }
            )

            mock_dependencies["registry"].get_device.assert_not_called()
            mock_integration.execute_command.assert_called_once_with("pump-1", "on", {})

    @pytest.mark.asyncio
    async def test_command_execution_exception(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test handling exception during command execution."""
        mock_dependencies["registry"].get_sensor_integration.return_value = "test_integration"
        mock_integration.execute_command.side_effect = Exception("Execution error")

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test_integration": mock_integration}

            await app._process_command(
                {
                    "id": "cmd-1",
                    "targetType": "sensor",
                    "targetId": "sensor-1",
                    "action": "read",
                }
            )

            call_args = mock_dependencies["mqtt_transport"].send_command_result.call_args
            assert call_args[0][0] == "cmd-1"
            assert call_args[0][1] is False
            assert "Error" in call_args[0][2]


class TestApplySettings:
    """Tests for Application._apply_settings()."""

    @pytest.mark.asyncio
    async def test_applies_settings_to_integrations(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test that settings are applied to all integrations."""
        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test": mock_integration}

            settings = {"key": "value"}
            await app._apply_settings(settings)

            mock_integration.apply_settings.assert_called_once_with(settings)

    @pytest.mark.asyncio
    async def test_handles_not_implemented(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test handling when integration doesn't support settings."""
        mock_integration.apply_settings.side_effect = NotImplementedError()

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test": mock_integration}

            # Should not raise
            await app._apply_settings({"key": "value"})

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, reset_application_singleton, mock_dependencies, mock_integration
    ):
        """Test handling exception during settings application."""
        mock_integration.apply_settings.side_effect = Exception("Settings error")

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"test": mock_integration}

            # Should not raise
            await app._apply_settings({"key": "value"})


class TestHandleWebRTCOffer:
    """Tests for _handle_webrtc_offer (the MQTT webrtc/offer broker)."""

    @pytest.mark.asyncio
    async def test_success_publishes_answer(self, reset_application_singleton, mock_dependencies):
        """A valid offer negotiates with the camera integration and publishes
        an ok answer echoing the sessionId."""
        mock_dependencies["mqtt_transport"].send_webrtc_answer = AsyncMock()
        camera = MagicMock()
        camera.negotiate_webrtc = AsyncMock(return_value="ANSWER_SDP")

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"CameraIntegration": camera}

            await app._handle_webrtc_offer(
                {"sessionId": "s-1", "streamId": "camera.tent1", "sdp": "OFFER"}
            )

        camera.negotiate_webrtc.assert_awaited_once_with("camera.tent1", "OFFER")
        mock_dependencies["mqtt_transport"].send_webrtc_answer.assert_awaited_once_with(
            {"sessionId": "s-1", "ok": True, "sdp": "ANSWER_SDP"}
        )

    @pytest.mark.asyncio
    async def test_missing_fields_publishes_failure(
        self, reset_application_singleton, mock_dependencies
    ):
        """An offer missing fields publishes a failure answer without touching
        the camera integration."""
        mock_dependencies["mqtt_transport"].send_webrtc_answer = AsyncMock()
        camera = MagicMock()
        camera.negotiate_webrtc = AsyncMock()

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"CameraIntegration": camera}

            await app._handle_webrtc_offer({"sessionId": "s-1", "streamId": "camera.tent1"})

        camera.negotiate_webrtc.assert_not_called()
        mock_dependencies["mqtt_transport"].send_webrtc_answer.assert_awaited_once_with(
            {"sessionId": "s-1", "ok": False, "error": "missing fields"}
        )

    @pytest.mark.asyncio
    async def test_no_camera_integration_publishes_failure(
        self, reset_application_singleton, mock_dependencies
    ):
        """With no camera integration loaded, a valid offer publishes failure."""
        mock_dependencies["mqtt_transport"].send_webrtc_answer = AsyncMock()

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {}

            await app._handle_webrtc_offer(
                {"sessionId": "s-1", "streamId": "camera.tent1", "sdp": "OFFER"}
            )

        mock_dependencies["mqtt_transport"].send_webrtc_answer.assert_awaited_once_with(
            {"sessionId": "s-1", "ok": False, "error": "no camera integration"}
        )

    @pytest.mark.asyncio
    async def test_negotiation_exception_publishes_failure(
        self, reset_application_singleton, mock_dependencies
    ):
        """A negotiation exception publishes a failure answer with str(e)."""
        mock_dependencies["mqtt_transport"].send_webrtc_answer = AsyncMock()
        camera = MagicMock()
        camera.negotiate_webrtc = AsyncMock(side_effect=ValueError("Unknown stream id"))

        with patch("app.main.signal.signal"):
            app = Application()
            app._integrations = {"CameraIntegration": camera}

            await app._handle_webrtc_offer(
                {"sessionId": "s-1", "streamId": "camera.bogus", "sdp": "OFFER"}
            )

        mock_dependencies["mqtt_transport"].send_webrtc_answer.assert_awaited_once_with(
            {"sessionId": "s-1", "ok": False, "error": "Unknown stream id"}
        )

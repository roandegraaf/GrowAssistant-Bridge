"""Main Application Module - contains the main application class and entry point."""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Optional

# Fix Python path to include parent directory
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from app.api_client import api_client
from app.auth import auth_manager
from app.config import config, init_logging
from app.integrations import (
    Integration,
    discover_integrations,
    get_integration_class,
    get_integration_class_by_config_key,
)
from app.queue_manager import queue_manager
from app.registry import registry

logger = logging.getLogger(__name__)


class Application:
    """Main application class managing lifecycle, integrations, and tasks.

    Uses singleton pattern to ensure only one instance exists.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Create or return the singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """Initialize the application."""
        if self._initialized:
            return

        self._integrations: dict[str, Integration] = {}
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._initialized = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            logger.info("Signal handlers registered in main thread")

        logger.info("Application initialized")

    def _signal_handler(self, sig, frame):
        """Handle signals for graceful shutdown."""
        logger.info(f"Received signal {sig}, shutting down...")

        if os.environ.get("WATCHDOG_MANAGED", "0") != "1":
            try:
                from app.watchdog import watchdog_manager

                watchdog_manager.set_deliberate_shutdown(True)
                watchdog_manager.stop(deliberate=True)
                logger.info("Watchdog manager stopped due to signal")
            except ImportError:
                logger.warning("Watchdog manager not found")

        asyncio.create_task(self.stop())

    async def start(self):
        """Start the application, loading integrations and starting all services."""
        if self._running:
            logger.warning("Application already running")
            return

        logger.info("Starting application")
        self._running = True
        self.loop = asyncio.get_running_loop()

        await auth_manager.start()

        if config.get("web.enabled", True):
            self._start_web_server()
            await asyncio.sleep(1)

        await self._handle_authentication()
        await queue_manager.start()

        await api_client.start()
        await api_client.start_command_polling()
        api_client.register_settings_callback(self._apply_settings)

        await self._load_integrations()
        self._create_tasks()

        logger.info("Application started")

    def _start_web_server(self):
        """Start the web server in a background thread."""
        logger.info("Starting web server...")
        from web.app import start_web_server

        web_thread = threading.Thread(target=start_web_server, args=(self,), daemon=True)
        web_thread.start()
        logger.info("Web server started in background thread")

    async def _handle_authentication(self):
        """Handle client authentication with the Spring API."""
        if auth_manager.is_authenticated():
            logger.info("Client already authenticated, validating credentials...")
            if await auth_manager.validate_credentials():
                logger.info("Credentials validated successfully")
                if auth_manager.is_ready_for_data():
                    logger.info("Client is ready to send data")
                    return
                logger.info("Client connected but space not created, waiting...")
                await auth_manager.wait_for_space_creation()
                return
            logger.warning("Stored credentials are invalid, re-registering client")

        logger.info("Registering client with API...")
        if not await auth_manager.register_client():
            logger.error("Failed to register client")
            print("\nFailed to register client with API. Check connection and try again.\n")
            await asyncio.sleep(2)
            sys.exit(1)

        auth_manager.display_auth_code()
        print("\nWaiting for you to enter this code in the GrowAssistant app...")
        print("Press Ctrl+C to cancel.\n")

        connection_timeout = config.get("api.connection_timeout", 300)
        if not await auth_manager.wait_for_connection(connection_timeout):
            print("\nTimeout waiting for connection.")
            print("You can get a new code from the web interface.\n")
            logger.info("Connection polling timed out - app will continue running")
            return  # Don't exit, just return and let the app continue

        print("\nConnection successful! Client connected to your environment.\n")

        _, status = await auth_manager.check_connection_status()
        if status == "connected":
            print("\nWaiting for space creation in the GrowAssistant app...")
            space_timeout = config.get("api.space_creation_timeout", 1800)
            if await auth_manager.wait_for_space_creation(space_timeout):
                print("\nSpace created! Client is now ready to send data.\n")
            else:
                print("\nTimeout waiting for space creation. Will check periodically.\n")

    async def stop(self):
        """Stop the application, cancelling tasks and stopping all services."""
        if not self._running:
            logger.warning("Application not running")
            return

        logger.info("Stopping application")
        self._running = False

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        for integration in self._integrations.values():
            try:
                await integration.disconnect()
            except Exception as e:
                logger.error(f"Error stopping integration {integration.name}: {e}")
        self._integrations.clear()

        await api_client.stop()
        await auth_manager.stop()
        await queue_manager.stop()

        logger.info("Application stopped")

    async def _apply_settings(self, settings: dict[str, Any]):
        """Apply settings received from the API to integrations."""
        logger.info(f"Applying settings from API: {settings}")

        for name, integration in self._integrations.items():
            try:
                await integration.apply_settings(settings)
                logger.debug(f"Applied settings to integration: {name}")
            except NotImplementedError:
                logger.debug(f"Integration {name} does not support settings")
            except Exception as e:
                logger.error(f"Error applying settings to integration {name}: {e}")

    async def _load_integrations(self):
        """Load and initialize integrations using self-registration pattern."""
        logger.info("Loading integrations")

        module_names = discover_integrations()
        logger.info(f"Discovered integration modules: {module_names}")

        integrations_config = config.get_section("integrations")

        for integration_type, integration_config in integrations_config.items():
            if not integration_config.get("enabled", False):
                logger.info(f"Integration '{integration_type}' is disabled, skipping")
                continue

            try:
                integration_class = get_integration_class_by_config_key(integration_type)
                if not integration_class:
                    # Fallback to legacy class name lookup
                    integration_class = get_integration_class(
                        f"{integration_type.capitalize()}Integration"
                    )

                if not integration_class:
                    logger.warning(f"Integration class for '{integration_type}' not found")
                    continue

                integration = integration_class(integration_config)
                if not await integration.connect():
                    logger.error(f"Failed to connect to integration '{integration.name}'")
                    continue

                self._integrations[integration.name] = integration
                integration.register_capabilities(registry)
                logger.info(f"Loaded integration: {integration.name}")

            except Exception as e:
                logger.exception(f"Error loading integration '{integration_type}': {e}")

        logger.info(f"Loaded {len(self._integrations)} integrations")

    def _create_tasks(self):
        """Create and start asyncio tasks."""
        logger.info("Creating application tasks")

        self._tasks.add(asyncio.create_task(self._data_collection_task()))
        self._tasks.add(asyncio.create_task(self._data_transmission_task()))
        self._tasks.add(asyncio.create_task(self._command_execution_task()))

        logger.info(f"Created {len(self._tasks)} application tasks")

    async def _data_collection_task(self):
        """Task for collecting data from integrations."""
        logger.info("Data collection task started")
        collection_interval = config.get("general.collection_interval", 60)

        try:
            while self._running:
                start_time = time.time()
                timestamp = int(time.time() * 1000)

                for name, integration in self._integrations.items():
                    try:
                        async for item in integration.receive_data():
                            if item:
                                item["timestamp"] = timestamp
                                item["integration"] = name
                                await queue_manager.put(item)
                    except Exception as e:
                        logger.error(f"Error collecting data from {name}: {e}")

                sleep_time = max(0, collection_interval - (time.time() - start_time))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            logger.info("Data collection task cancelled")
        except Exception as e:
            logger.error(f"Error in data collection task: {e}")

        logger.info("Data collection task stopped")

    async def _data_transmission_task(self):
        """Task for transmitting data to the API."""
        logger.info("Data transmission task started")

        batch_size = config.get("api.batch_size", 100)
        transmission_interval = config.get("api.transmission_interval", 60)
        consecutive_failures = 0
        max_failures_before_check = 5

        try:
            while self._running:
                start_time = time.time()

                if not auth_manager.is_ready_for_data():
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures_before_check:
                        logger.info("Checking if space has been created...")
                        _, status = await auth_manager.check_connection_status()
                        if status == "ready":
                            logger.info("Space created, resuming data transmission")
                            consecutive_failures = 0
                        else:
                            logger.info(f"Space not created yet ({status}), waiting...")
                    await asyncio.sleep(transmission_interval)
                    continue

                consecutive_failures = 0
                data_points = await queue_manager.get_data_points(batch_size)

                if data_points:
                    success, message = await api_client.send_data(data_points)
                    if success:
                        await queue_manager.mark_processed(data_points)
                    else:
                        await queue_manager.requeue_data_points(data_points)
                        logger.warning(f"Failed to send data: {message}")

                sleep_time = max(0, transmission_interval - (time.time() - start_time))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            logger.info("Data transmission task cancelled")
        except Exception as e:
            logger.error(f"Error in data transmission task: {e}")

        logger.info("Data transmission task stopped")

    async def _command_execution_task(self):
        """Task for executing commands from the API."""
        logger.info("Command execution task started")

        consecutive_failures = 0
        max_failures_before_check = 5

        try:
            while self._running:
                if not auth_manager.is_ready_for_data():
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures_before_check:
                        logger.info("Checking if space has been created...")
                        _, status = await auth_manager.check_connection_status()
                        if status == "ready":
                            logger.info("Space created, resuming command processing")
                            consecutive_failures = 0
                        else:
                            logger.info(f"Space not created yet ({status}), waiting...")
                    await asyncio.sleep(5)
                    continue

                consecutive_failures = 0
                command = await api_client.get_command(timeout=1.0)
                if command:
                    await self._process_command(command)

        except asyncio.CancelledError:
            logger.info("Command execution task cancelled")
        except Exception as e:
            logger.error(f"Error in command execution task: {e}")

        logger.info("Command execution task stopped")

    async def _process_command(self, command: dict[str, Any]):
        """Process a command from the API."""
        logger.info(f"Processing command: {command}")

        command_id = command.get("id")
        if not command_id:
            logger.error("Command is missing ID")
            return

        target_type = command.get("targetType")
        target_id = command.get("targetId")
        action = command.get("action")
        payload = command.get("payload", {})

        if not all([target_type, target_id, action]):
            logger.error(f"Command missing required fields: {command}")
            await api_client.send_command_result(command_id, False, "Missing required fields")
            return

        try:
            if target_type == "sensor":
                integration_name = registry.get_sensor_integration(target_id)
            elif target_type == "actuator":
                integration_name = registry.get_actuator_integration(target_id)
            else:
                logger.error(f"Unknown target type: {target_type}")
                await api_client.send_command_result(
                    command_id, False, f"Unknown target type: {target_type}"
                )
                return

            if not integration_name or integration_name not in self._integrations:
                logger.error(f"No integration found for {target_type} {target_id}")
                await api_client.send_command_result(
                    command_id, False, f"No integration for {target_type} {target_id}"
                )
                return

            integration = self._integrations[integration_name]
            success = await integration.execute_command(target_id, action, payload)

            result_msg = "Command executed successfully" if success else "Command execution failed"
            await api_client.send_command_result(command_id, success, result_msg)
            logger.info(f"Command {command_id}: success={success}")

        except Exception as e:
            logger.error(f"Error processing command: {e}")
            await api_client.send_command_result(command_id, False, f"Error: {e}")


async def main():
    """Main entry point."""
    init_logging()

    is_watchdog_child = os.environ.get("WATCHDOG_MANAGED", "0") == "1"

    if not is_watchdog_child:
        try:
            from app.watchdog import watchdog_manager

            logger.info("Starting watchdog manager...")
            watchdog_manager.start()
            logger.info("Watchdog started to monitor this process")
        except ImportError:
            logger.warning("Watchdog manager not found, continuing without")
    else:
        logger.info("Running as watchdog-managed process")

    app_instance = Application()

    try:
        await app_instance.start()
        await asyncio.sleep(1)

        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        if not is_watchdog_child:
            try:
                from app.watchdog import watchdog_manager

                watchdog_manager.stop(deliberate=True)
                logger.info("Watchdog manager stopped")
            except ImportError:
                pass

    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")

    finally:
        if app_instance is not None:
            await app_instance.stop()


if __name__ == "__main__":
    asyncio.run(main())

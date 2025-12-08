"""
Main Application Module.

This module contains the main application class and entry point.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
import time

# Fix Python path to include parent directory
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from typing import Any, Optional

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

# Remove thread-safe access logic as it's no longer the primary way
# _app_lock = threading.Lock()
# _web_app_instance = None

logger = logging.getLogger(__name__)


class Application:
    """Main application class.

    This class manages the lifecycle of the application, including startup, shutdown,
    integration loading, and task management.

    Attributes:
        _instance: Singleton instance of the Application.
        _integrations: Dictionary of loaded integration instances.
        _running: Whether the application is running.
        _tasks: Set of running asyncio tasks.
        loop: Optional[asyncio.AbstractEventLoop]: Store the loop
    """

    _instance = None
    _lock = threading.Lock()  # Keep lock for singleton creation

    def __new__(cls):
        """Create or return the singleton instance.

        Returns:
            Application: The singleton instance.
        """
        # Keep lock for ensuring single instance creation
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
                # Remove storing global reference
                # global _web_app_instance
                # _web_app_instance = cls._instance
            return cls._instance

    def __init__(self):
        """Initialize the application."""
        if self._initialized:
            return

        self._integrations: dict[str, Integration] = {}
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._initialized = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # Store the loop

        # Set up signal handlers for graceful shutdown
        # Only set up signal handlers if we're in the main thread
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            logger.info("Signal handlers registered in main thread")

        logger.info("Application initialized")

    def _signal_handler(self, sig, frame):
        """Handle signals for graceful shutdown.

        Args:
            sig: Signal number.
            frame: Current stack frame.
        """
        logger.info(f"Received signal {sig}, shutting down...")

        # Check if this is a watchdog-managed process
        is_watchdog_child = os.environ.get("WATCHDOG_MANAGED", "0") == "1"

        # If this is the main process, also stop the watchdog
        if not is_watchdog_child:
            try:
                from app.watchdog import watchdog_manager

                watchdog_manager.set_deliberate_shutdown(True)
                watchdog_manager.stop(deliberate=True)
                logger.info("Watchdog manager signaled to stop due to signal")
            except ImportError:
                logger.warning("Watchdog manager not found for signal handling")

        # Create task to stop the application
        asyncio.create_task(self.stop())

    async def start(self):
        """Start the application.

        This method loads integrations, starts the queue manager and API client,
        and creates tasks for data collection, transmission, and command execution.
        """
        if self._running:
            logger.warning("Application already running")
            return

        logger.info("Starting application")
        self._running = True
        self.loop = asyncio.get_running_loop()  # Get and store the running loop

        # Start the auth manager first
        await auth_manager.start()

        # Start the web server immediately if enabled, so it's available during setup
        if config.get("web.enabled", True):
            logger.info("Starting web server...")
            # Import here to avoid circular imports
            import threading

            from web.app import start_web_server

            # Start the web server in a separate thread, passing the app instance
            web_thread = threading.Thread(target=start_web_server, args=(self,))
            web_thread.daemon = True
            web_thread.start()
            logger.info("Web server started in background thread")

            # Give the web server a moment to initialize
            await asyncio.sleep(1)

        # Check authentication and handle registration if needed
        await self._handle_authentication()

        # Start the queue manager
        await queue_manager.start()

        # Start the API client
        await api_client.start()
        await api_client.start_command_polling()

        # Register settings callback with API client
        api_client.register_settings_callback(self._apply_settings)

        # Load integrations
        await self._load_integrations()

        # Create and start tasks
        self._create_tasks()

        logger.info("Application started")

    async def _handle_authentication(self):
        """Handle client authentication with the Spring API."""
        # Check if we already have credentials
        if auth_manager.is_authenticated():
            logger.info("Client already authenticated, validating credentials...")

            # Validate the credentials
            valid = await auth_manager.validate_credentials()
            if valid:
                logger.info("Credentials validated successfully")

                # Check if we're ready to send data (space is created)
                if auth_manager.is_ready_for_data():
                    logger.info("Client is ready to send data")
                    return
                else:
                    logger.info(
                        "Client is connected but space not created yet, waiting for space creation..."
                    )
                    # Wait for space creation
                    await auth_manager.wait_for_space_creation()
                    return

            logger.warning("Stored credentials are invalid, re-registering client")

        # Register the client
        logger.info("Registering client with API...")
        success = await auth_manager.register_client()
        if not success:
            logger.error("Failed to register client")
            print(
                "\nFailed to register client with the API. Please check your connection and try again.\n"
            )
            # Wait before exiting to allow logs to be written
            await asyncio.sleep(2)
            sys.exit(1)

        # Display the authentication code to the user
        auth_manager.display_auth_code()

        print("\nWaiting for you to enter this code in the GrowAssistant app...")
        print("Press Ctrl+C to cancel.\n")

        # Wait for the client to be connected to an environment (either 204 or 200)
        connection_timeout = config.get("api.connection_timeout", 300)  # 5 minutes default
        connected = await auth_manager.wait_for_connection(connection_timeout)

        if not connected:
            print("\nTimeout waiting for connection. Please try again.\n")
            # Wait before exiting to allow logs to be written
            await asyncio.sleep(2)
            sys.exit(1)

        print("\nConnection successful! The client is now connected to your environment.\n")

        # Now wait for space creation if needed
        _, status = await auth_manager.check_connection_status()
        if status == "connected":
            print("\nWaiting for space creation in the GrowAssistant app...")
            space_timeout = config.get("api.space_creation_timeout", 1800)  # 30 minutes default
            space_created = await auth_manager.wait_for_space_creation(space_timeout)

            if not space_created:
                print(
                    "\nTimeout waiting for space creation. The application will continue to check periodically.\n"
                )
                # Don't exit here, let the application continue and check later
            else:
                print("\nSpace created successfully! The client is now ready to send data.\n")

    async def stop(self):
        """Stop the application.

        This method cancels all tasks, stops integrations, and stops
        the queue manager and API client.
        """
        if not self._running:
            logger.warning("Application not running")
            return

        logger.info("Stopping application")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        # Stop integrations
        for integration in self._integrations.values():
            try:
                await integration.disconnect()
            except Exception as e:
                logger.error(f"Error stopping integration {integration.name}: {e}")

        self._integrations.clear()

        # Stop the API client
        await api_client.stop()

        # Stop the auth manager
        await auth_manager.stop()

        # Stop the queue manager
        await queue_manager.stop()

        logger.info("Application stopped")

    async def _apply_settings(self, settings: dict[str, Any]):
        """Apply settings received from the API to integrations.

        Args:
            settings: Settings dictionary containing light, climate, tank, etc.
        """
        logger.info(f"Applying settings from API: {settings}")

        # Apply settings to all integrations
        for name, integration in self._integrations.items():
            try:
                await integration.apply_settings(settings)
                logger.debug(f"Applied settings to integration: {name}")
            except NotImplementedError:
                # Integration doesn't support settings, skip
                logger.debug(f"Integration {name} does not support settings")
            except Exception as e:
                logger.error(f"Error applying settings to integration {name}: {e}")

    async def _load_integrations(self):
        """Load and initialize integrations.

        This method uses the new self-registration pattern where integrations
        register their own capabilities instead of hardcoded handlers.
        """
        logger.info("Loading integrations")

        # Discover available integration modules
        module_names = discover_integrations()
        logger.info(f"Discovered integration modules: {module_names}")

        # Load configured integrations
        integrations_config = config.get_section("integrations")

        for integration_type, integration_config in integrations_config.items():
            if not integration_config.get("enabled", False):
                logger.info(f"Integration '{integration_type}' is disabled, skipping")
                continue

            try:
                # Get integration class by config key (no hardcoding!)
                integration_class = get_integration_class_by_config_key(integration_type)

                if not integration_class:
                    # Fallback to legacy class name lookup for backward compatibility
                    class_name = f"{integration_type.capitalize()}Integration"
                    integration_class = get_integration_class(class_name)

                if not integration_class:
                    logger.warning(f"Integration class for '{integration_type}' not found")
                    continue

                # Create the integration instance
                integration = integration_class(integration_config)

                # Connect to the integration
                success = await integration.connect()
                if not success:
                    logger.error(f"Failed to connect to integration '{integration.name}'")
                    continue

                # Store the integration
                self._integrations[integration.name] = integration

                # Self-registration: integration registers its own capabilities
                integration.register_capabilities(registry)

                logger.info(f"Loaded integration: {integration.name}")

            except Exception as e:
                logger.exception(f"Error loading integration '{integration_type}': {e}")

        logger.info(f"Loaded {len(self._integrations)} integrations")

    # NOTE: The old _register_integration_capabilities and its handlers
    # (_register_gpio_capabilities, _register_mqtt_capabilities, etc.)
    # have been removed. Integrations now self-register via their
    # register_capabilities() method, which is called after connect().

    def _create_tasks(self):
        """Create and start asyncio tasks."""
        logger.info("Creating application tasks")

        # Data collection task
        collection_task = asyncio.create_task(self._data_collection_task())
        self._tasks.add(collection_task)

        # Data transmission task
        transmission_task = asyncio.create_task(self._data_transmission_task())
        self._tasks.add(transmission_task)

        # Command execution task
        command_task = asyncio.create_task(self._command_execution_task())
        self._tasks.add(command_task)

        logger.info(f"Created {len(self._tasks)} application tasks")

    async def _data_collection_task(self):
        """Task for collecting data from integrations."""
        logger.info("Data collection task started")

        collection_interval = config.get("general.collection_interval", 60)  # seconds

        try:
            while self._running:
                start_time = time.time()

                # Collect data from each integration
                for name, integration in self._integrations.items():
                    try:
                        # The receive_data method returns an async generator, so we need to iterate over it
                        timestamp = int(time.time() * 1000)  # milliseconds
                        async for item in integration.receive_data():
                            if item:
                                # Add metadata and queue the data
                                item["timestamp"] = timestamp
                                item["integration"] = name
                                await queue_manager.put(item)

                    except Exception as e:
                        logger.error(f"Error collecting data from {name}: {e}")

                # Sleep until the next collection interval
                elapsed = time.time() - start_time
                sleep_time = max(0, collection_interval - elapsed)

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

        # Get configuration
        batch_size = config.get("api.batch_size", 100)
        transmission_interval = config.get("api.transmission_interval", 60)  # seconds

        # Track consecutive failures for space creation check
        consecutive_failures = 0
        max_failures_before_check = 5  # Check space creation after this many failures

        try:
            while self._running:
                start_time = time.time()

                # Check if we're ready to send data
                if not auth_manager.is_ready_for_data():
                    consecutive_failures += 1

                    # After several failures, check if space is created
                    if consecutive_failures >= max_failures_before_check:
                        logger.info("Checking if space has been created...")
                        _, status = await auth_manager.check_connection_status()

                        if status == "ready":
                            logger.info("Space has been created, resuming data transmission")
                            consecutive_failures = 0
                        else:
                            logger.info(
                                f"Space not created yet (status: {status}), waiting for next transmission cycle"
                            )

                    await asyncio.sleep(transmission_interval)
                    continue

                # Reset consecutive failures counter if we get here
                consecutive_failures = 0

                # Get a batch of data points from the queue
                data_points = await queue_manager.get_data_points(batch_size)

                if data_points:
                    # Send the data to the API
                    success, message = await api_client.send_data(data_points)

                    if success:
                        # Mark the data points as processed
                        await queue_manager.mark_processed(data_points)
                    else:
                        # Put the data points back in the queue for retry
                        await queue_manager.requeue_data_points(data_points)
                        logger.warning(f"Failed to send data: {message}")

                # Sleep until the next transmission interval
                elapsed = time.time() - start_time
                sleep_time = max(0, transmission_interval - elapsed)

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

        # Track consecutive failures for space creation check
        consecutive_failures = 0
        max_failures_before_check = 5  # Check space creation after this many failures

        try:
            while self._running:
                # Check if we're ready to process commands
                if not auth_manager.is_ready_for_data():
                    consecutive_failures += 1

                    # After several failures, check if space is created
                    if consecutive_failures >= max_failures_before_check:
                        logger.info("Checking if space has been created...")
                        _, status = await auth_manager.check_connection_status()

                        if status == "ready":
                            logger.info("Space has been created, resuming command processing")
                            consecutive_failures = 0
                        else:
                            logger.info(
                                f"Space not created yet (status: {status}), waiting for next command check"
                            )

                    # Wait a bit before checking again
                    await asyncio.sleep(5)
                    continue

                # Reset consecutive failures counter if we get here
                consecutive_failures = 0

                # Get a command from the queue
                command = await api_client.get_command(timeout=1.0)

                if command:
                    # Process the command
                    await self._process_command(command)

        except asyncio.CancelledError:
            logger.info("Command execution task cancelled")

        except Exception as e:
            logger.error(f"Error in command execution task: {e}")

        logger.info("Command execution task stopped")

    async def _process_command(self, command: dict[str, Any]):
        """Process a command from the API.

        Args:
            command: The command to process.
        """
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
            logger.error(f"Command is missing required fields: {command}")
            await api_client.send_command_result(
                command_id, False, "Command is missing required fields"
            )
            return

        # Use the registry to find the integration for the target
        try:
            integration_name = None

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
                    command_id, False, f"No integration found for {target_type} {target_id}"
                )
                return

            # Get the integration
            integration = self._integrations[integration_name]

            # Execute the command using the unified interface
            # (This fixes the signature mismatch bug in the original code)
            success = await integration.execute_command(target_id, action, payload)

            # Send the result back to the API
            result_message = (
                "Command executed successfully" if success else "Command execution failed"
            )
            await api_client.send_command_result(command_id, success, result_message)

            logger.info(
                f"Command {command_id} executed: success={success}, message={result_message}"
            )

        except Exception as e:
            logger.error(f"Error processing command: {e}")
            await api_client.send_command_result(command_id, False, f"Error: {str(e)}")


async def main():
    """Main entry point."""
    # Initialize logging
    init_logging()

    # Check if this is running as a watchdog-managed process
    is_watchdog_child = os.environ.get("WATCHDOG_MANAGED", "0") == "1"

    # Start the watchdog if this is not already a watchdog-managed process
    if not is_watchdog_child:
        try:
            from app.watchdog import watchdog_manager

            logger.info("Starting watchdog manager...")

            # Start the watchdog (it will run in a separate process that monitors this one)
            watchdog_manager.start()
            logger.info("Watchdog started to monitor this process")
        except ImportError:
            logger.warning("Watchdog manager not found, continuing without watchdog")
    else:
        logger.info("Running as watchdog-managed process")

    # Create and start the application
    app_instance = Application()  # Get the instance

    try:
        # Start the application and wait for it to fully initialize
        await app_instance.start()

        # Wait a short moment to ensure integrations are fully loaded
        await asyncio.sleep(1)

        # Keep the main task running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

        # Signal watchdog to stop if this is not a watchdog-managed process
        if not is_watchdog_child:
            try:
                from app.watchdog import watchdog_manager

                watchdog_manager.stop(deliberate=True)
                logger.info("Watchdog manager stopped due to keyboard interrupt")
            except ImportError:
                pass

    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")

    finally:
        # Use the local variable for stop
        if "app_instance" in locals() and app_instance is not None:
            await app_instance.stop()


if __name__ == "__main__":
    asyncio.run(main())

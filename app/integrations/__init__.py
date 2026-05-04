"""
Integration Base Module.

This module defines the base Integration class that all integrations must inherit from,
as well as the mechanisms for dynamically loading integration modules.
"""

import abc
import importlib
import importlib.util
import logging
import os
import pkgutil
import sys
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Union

from pydantic import BaseModel, ValidationError

from app.api_client import api_client
from app.api_types import (
    ActionType,
    LogType,
    ProblemStatus,
    ProblemType,
    create_action_response,
    create_data_log,
    create_problem,
)

if TYPE_CHECKING:
    from app.registry import DeviceRegistry


logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""


class Integration(abc.ABC):
    """Base class for all integrations.

    All device integrations should inherit from this class and implement
    the required methods.

    Class Attributes:
        CONFIG_SCHEMA: Optional Pydantic model for config validation.
                       If set, config will be validated on instantiation.
    """

    # Subclasses can override this with a Pydantic model for config validation
    CONFIG_SCHEMA: ClassVar[Optional[type[BaseModel]]] = None

    def __init__(self, config: dict[str, Any]):
        """Initialize the integration with configuration.

        Args:
            config: Configuration dictionary specific to this integration.

        Raises:
            ConfigurationError: If config validation fails and CONFIG_SCHEMA is set.
        """
        self.config = config
        self.name = self.__class__.__name__
        self._validated_config: Optional[BaseModel] = None

        # Validate config if schema is defined
        if self.CONFIG_SCHEMA is not None:
            try:
                self._validated_config = self.CONFIG_SCHEMA.model_validate(config)
            except ValidationError as e:
                logger.error(f"Configuration validation failed for {self.name}: {e}")
                raise ConfigurationError(f"Invalid configuration for {self.name}: {e}") from e

    @classmethod
    def get_config_key(cls) -> str:
        """Get the configuration key for this integration.

        This is the key used in config.yaml under 'integrations:'.
        Default implementation derives from class name:
            - HTTPIntegration -> "http"
            - MQTTIntegration -> "mqtt"
            - GPIOIntegration -> "gpio"

        Subclasses can override for custom mapping.

        Returns:
            str: The configuration key (lowercase).
        """
        name = cls.__name__.removesuffix("Integration")
        return name.lower()

    @property
    def validated_config(self) -> Optional[BaseModel]:
        """Get the validated configuration object.

        Returns:
            The validated Pydantic model if CONFIG_SCHEMA was set, else None.
        """
        return self._validated_config

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register this integration's capabilities with the device registry.

        This method is called after connect() succeeds. Subclasses should
        override this to register their sensors and actuators.

        The default implementation handles integrations with a 'devices' config
        by calling registry.register_integration_by_devices().

        Args:
            registry: The DeviceRegistry instance to register with.
        """
        # Default implementation for integrations with 'devices' config
        if "devices" in self.config:
            registry.register_integration_by_devices(self.name, self.config.get("devices", {}))

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command on a target device.

        This is the unified command interface that properly wraps send_data().
        It fixes the signature mismatch in the original command processing.

        Subclasses can override for custom command handling.

        Args:
            target_id: The target device identifier.
            action: The action to perform (e.g., "on", "off", "set").
            payload: Additional command parameters.

        Returns:
            bool: True if command executed successfully, False otherwise.
        """
        return await self.send_data({"target_id": target_id, "action": action, **payload})

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the device/service.

        Returns:
            bool: True if connection was successful, False otherwise.
        """
        pass

    @abc.abstractmethod
    async def send_data(self, data: dict[str, Any]) -> bool:
        """Send data/command to the device/service.

        Args:
            data: Data to send to the device/service.

        Returns:
            bool: True if send was successful, False otherwise.
        """
        pass

    @abc.abstractmethod
    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        """Receive data from the device/service.

        Yields:
            Dict[str, Any]: Data received from the device/service.
        """
        pass

    @abc.abstractmethod
    async def get_device_data(self) -> dict[str, Any]:
        """Get the current data/state for all devices managed by this integration.

        Returns:
            Dict[str, Any]: A dictionary mapping device names to their current values/states.
        """
        pass

    # New methods for API data format
    def log_data(
        self,
        log_type: Union[LogType, str],
        value: Union[str, float, int],
        log_date=None,
        device_id=None,
    ):
        """Log data to the API.

        Args:
            log_type: Type of data being logged
            value: Value to log
            log_date: Optional timestamp (defaults to now)
            device_id: Optional device identifier for multiple devices of same type
        """
        api_client.add_data_log(log_type, value, log_date, device_id)

    def report_problem(
        self,
        problem_type: Union[ProblemType, str],
        status: Union[ProblemStatus, str],
        description: str,
        priority: int = 0,
        user_can_resolve: bool = True,
        resolved: bool = False,
        problem_id: Optional[str] = None,
    ):
        """Report a problem to the API.

        Args:
            problem_type: Type of problem
            status: Problem status category
            description: Description of the problem
            priority: Priority (0-100)
            user_can_resolve: Whether user can resolve
            resolved: Whether already resolved
            problem_id: Optional ID
        """
        api_client.add_problem(
            problem_type, status, description, priority, user_can_resolve, resolved, problem_id
        )

    def register_action_handler(self, action_type: Union[ActionType, str], handler: Callable):
        """Register a handler for API actions.

        Args:
            action_type: Type of action to handle
            handler: Callback function(action_data) -> bool
        """
        api_client.register_action_handler(action_type, handler)

    def acknowledge_action(self, action_id: str, received: bool = True, resolved: bool = False):
        """Acknowledge an action from the API.

        Args:
            action_id: ID of the action
            received: Whether received
            resolved: Whether completed
        """
        api_client.acknowledge_action(action_id, received, resolved)

    async def handle_action(self, action_data: dict[str, Any]) -> bool:
        """Handle an action requested by the API.

        This method should be implemented by integrations to handle
        actions requested by the API.

        Args:
            action_data: Action data from the API

        Returns:
            bool: True if action was handled successfully
        """
        # Default implementation returns False
        logger.warning(f"Integration {self.name} does not implement handle_action")
        return False

    async def apply_settings(self, settings: dict[str, Any]) -> bool:
        """Apply settings received from the API.

        This method can be implemented by integrations to handle
        settings updates from the API (light schedules, climate settings, etc.).

        Args:
            settings: Settings dictionary containing:
                - rdh_mode: bool
                - status: str
                - light: dict with 'day' and 'night' settings
                - climate: dict with 'temperature', 'humidity', 'baseFanSpeed'
                - tank: dict with 'waters', 'ph', 'amountML'

        Returns:
            bool: True if settings were applied successfully

        Raises:
            NotImplementedError: If integration doesn't support settings
        """
        # Default implementation raises NotImplementedError to signal
        # that this integration doesn't support settings
        raise NotImplementedError(f"Integration {self.name} does not support settings")

    async def disconnect(self):
        """Disconnect from the device/service and clean up resources.

        This method should be implemented by integrations to properly clean up
        resources when shutting down.
        """


# Registry: class name -> class
_integration_classes: dict[str, type[Integration]] = {}

# Registry: config key -> class (for lookup by config.yaml key)
_integration_by_config_key: dict[str, type[Integration]] = {}


def register_integration(cls: type[Integration]) -> type[Integration]:
    """Decorator to register an integration class.

    Registers the class by both class name and config key for flexible lookup.
    This enables looking up integrations by their config.yaml key without
    hardcoding the mapping.

    Args:
        cls: Integration class to register.

    Returns:
        Type[Integration]: The registered class.
    """
    # Register by class name
    _integration_classes[cls.__name__] = cls

    # Register by config key
    config_key = cls.get_config_key()
    _integration_by_config_key[config_key] = cls

    logger.info(f"Registered integration: {cls.__name__} (config_key: {config_key})")
    return cls


def get_integration_class(name: str) -> Optional[type[Integration]]:
    """Get an integration class by class name.

    Args:
        name: Name of the integration class (e.g., "MQTTIntegration").

    Returns:
        Optional[Type[Integration]]: The integration class, or None if not found.
    """
    return _integration_classes.get(name)


def get_integration_class_by_config_key(config_key: str) -> Optional[type[Integration]]:
    """Get an integration class by its configuration key.

    This is the primary way to look up integrations when loading from config.
    The config key is the key used in config.yaml (e.g., "mqtt", "http", "gpio").

    Args:
        config_key: The configuration key (e.g., "mqtt", "http").

    Returns:
        Optional[Type[Integration]]: The integration class, or None if not found.
    """
    return _integration_by_config_key.get(config_key.lower())


def get_all_integration_classes() -> dict[str, type[Integration]]:
    """Get all registered integration classes.

    Returns:
        Dict[str, Type[Integration]]: Dictionary of integration class names to classes.
    """
    return _integration_classes.copy()


def get_all_config_keys() -> list[str]:
    """Get all registered config keys.

    Returns:
        List[str]: List of all registered configuration keys.
    """
    return list(_integration_by_config_key.keys())


def _load_from_directory(directory_path: str) -> list[str]:
    """Load Python modules from a directory path.

    Args:
        directory_path: Path to directory containing Python modules.

    Returns:
        List[str]: List of successfully loaded module names.
    """
    if not os.path.exists(directory_path):
        logger.warning(f"External integration directory does not exist: {directory_path}")
        return []

    logger.info(f"Scanning for integration modules in: {directory_path}")

    if directory_path not in sys.path:
        sys.path.append(directory_path)

    loaded_modules = []
    for file in os.listdir(directory_path):
        if not file.endswith(".py") or file.startswith("_"):
            continue

        module_name = file.removesuffix(".py")
        module_path = os.path.join(directory_path, file)

        try:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                loaded_modules.append(module_name)
                logger.info(f"Successfully loaded external module: {module_name}")
        except ImportError as e:
            logger.error(f"Error importing external module {module_name}: {e}")
        except Exception as e:
            logger.error(f"Error loading external module {module_name}: {e}")

    return loaded_modules


def discover_integrations() -> list[str]:
    """Discover and load all available integration modules.

    This function searches for modules in the 'integrations' package and imports them.
    It also searches for external integrations in the configured external integrations directory.
    Each module should register its integration class(es) using the register_integration decorator.

    Returns:
        List[str]: List of discovered integration module names.
    """
    package_dir = os.path.dirname(__file__)
    module_names = []

    # First, load built-in integrations
    for _, name, is_pkg in pkgutil.iter_modules([package_dir]):
        if is_pkg:  # Only load packages (directories with __init__.py)
            try:
                importlib.import_module(f"{__name__}.{name}")
                module_names.append(name)
                logger.info(f"Discovered built-in integration module: {name}")
            except ImportError as e:
                logger.error(f"Error importing built-in integration module {name}: {e}")

    # Then, load external integrations from the external directory
    from app.config import config

    external_dir = config.get("general.external_integrations_dir", "external_integrations")

    # Make path absolute if it's not already
    if not os.path.isabs(external_dir):
        # Get the project root directory (two levels up from this file)
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        external_dir = os.path.join(app_root, external_dir)

    # Load from external directory
    external_modules = _load_from_directory(external_dir)
    module_names.extend(external_modules)

    return module_names

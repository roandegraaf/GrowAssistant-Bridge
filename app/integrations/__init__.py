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
from typing import Any, Dict, Generator, List, Optional, Type, Callable, Union

from app.api_types import (
    ActionType, LogType, ProblemType, ProblemStatus,
    create_data_log, create_problem, create_action_response
)
from app.api_client import api_client


logger = logging.getLogger(__name__)


class Integration(abc.ABC):
    """Base class for all integrations.
    
    All device integrations should inherit from this class and implement
    the required methods.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the integration with configuration.
        
        Args:
            config: Configuration dictionary specific to this integration.
        """
        self.config = config
        self.name = self.__class__.__name__
    
    @abc.abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the device/service.
        
        Returns:
            bool: True if connection was successful, False otherwise.
        """
        pass
    
    @abc.abstractmethod
    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data/command to the device/service.
        
        Args:
            data: Data to send to the device/service.
            
        Returns:
            bool: True if send was successful, False otherwise.
        """
        pass
    
    @abc.abstractmethod
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from the device/service.
        
        Yields:
            Dict[str, Any]: Data received from the device/service.
        """
        pass

    @abc.abstractmethod
    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data/state for all devices managed by this integration.
        
        Returns:
            Dict[str, Any]: A dictionary mapping device names to their current values/states.
        """
        pass
        
    # New methods for API data format
    def log_data(self, log_type: Union[LogType, str], value: Union[str, float, int], log_date=None, pump_num=None):
        """Log data to the API.

        Args:
            log_type: Type of data being logged
            value: Value to log
            log_date: Optional timestamp (defaults to now)
            pump_num: Optional pump number (only for pump-related logs)
        """
        api_client.add_data_log(log_type, value, log_date, pump_num)
        
    def report_problem(self, problem_type: Union[ProblemType, str], status: Union[ProblemStatus, str], 
                     description: str, priority: int = 0, user_can_resolve: bool = True, 
                     resolved: bool = False, problem_id: Optional[str] = None):
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
            problem_type, status, description, priority,
            user_can_resolve, resolved, problem_id
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
        
    async def handle_action(self, action_data: Dict[str, Any]) -> bool:
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

    async def apply_settings(self, settings: Dict[str, Any]) -> bool:
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
        # Default implementation does nothing
        pass


_integration_classes: Dict[str, Type[Integration]] = {}


def register_integration(cls: Type[Integration]) -> Type[Integration]:
    """Decorator to register an integration class.
    
    Args:
        cls: Integration class to register.
        
    Returns:
        Type[Integration]: The registered class.
    """
    _integration_classes[cls.__name__] = cls
    logger.info(f"Registered integration: {cls.__name__}")
    return cls


def get_integration_class(name: str) -> Optional[Type[Integration]]:
    """Get an integration class by name.
    
    Args:
        name: Name of the integration class.
        
    Returns:
        Optional[Type[Integration]]: The integration class, or None if not found.
    """
    return _integration_classes.get(name)


def get_all_integration_classes() -> Dict[str, Type[Integration]]:
    """Get all registered integration classes.
    
    Returns:
        Dict[str, Type[Integration]]: Dictionary of integration class names to classes.
    """
    return _integration_classes.copy()


def _load_from_directory(directory_path: str) -> List[str]:
    """Load Python modules from a directory path.
    
    Args:
        directory_path: Path to directory containing Python modules.
        
    Returns:
        List[str]: List of successfully loaded module names.
    """
    loaded_modules = []
    
    if not os.path.exists(directory_path):
        logger.warning(f"External integration directory does not exist: {directory_path}")
        return loaded_modules
        
    logger.info(f"Scanning for integration modules in: {directory_path}")
    
    # Add the directory to sys.path if not already there
    if directory_path not in sys.path:
        sys.path.append(directory_path)
    
    # Walk through all Python files in the directory
    for file in os.listdir(directory_path):
        if file.endswith('.py') and not file.startswith('_'):
            module_name = file[:-3]  # Remove .py extension
            try:
                # Construct the full path to the module
                module_path = os.path.join(directory_path, file)
                
                # Load the module
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                if spec is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    loaded_modules.append(module_name)
                    logger.info(f"Successfully loaded external module: {module_name}")
            except ImportError as e:
                logger.error(f"Error importing external module {module_name}: {e}")
            except Exception as e:
                logger.error(f"Error loading external module {module_name}: {e}")
    
    return loaded_modules


def discover_integrations() -> List[str]:
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
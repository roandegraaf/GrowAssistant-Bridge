"""
Device Registry Module.

This module manages the registry of sensors and actuators,
mapping them to the integrations that can handle them.
"""

import logging
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)


class DeviceRegistry:
    """Registry of sensors and actuators.
    
    This class manages the mapping between sensors/actuators and the integrations
    that can handle them. It allows looking up which integration to use
    for a specific device.
    
    Attributes:
        _instance: Singleton instance of the DeviceRegistry.
        _sensors: Dictionary mapping sensor names to integration names.
        _actuators: Dictionary mapping actuator names to integration names.
    """
    
    _instance = None
    
    def __new__(cls):
        """Create or return the singleton instance.
        
        Returns:
            DeviceRegistry: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(DeviceRegistry, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the device registry."""
        if self._initialized:
            return
            
        # Maps sensor names to integration names
        # Example: {"temperature": "mqtt", "humidity": "http"}
        self._sensors: Dict[str, str] = {}
        
        # Maps actuator names to integration names
        # Example: {"pump": "gpio", "light": "mqtt"}
        self._actuators: Dict[str, str] = {}
        
        # Maps device types to common actions they support
        # Example: {"pump": ["on", "off"], "light": ["on", "off", "dim"]}
        self._device_type_actions: Dict[str, List[str]] = {
            "pump": ["on", "off"],
            "light": ["on", "off", "dim"],
            "fan": ["on", "off", "speed"],
            "heater": ["on", "off", "temperature"],
            "humidity": ["on", "off", "level"],
            "temperature": [],  # Sensor, no actions
            "water_level": [],  # Sensor, no actions
            "light_sensor": [],  # Sensor, no actions
        }
        
        self._initialized = True
        logger.info("Device Registry initialized")
    
    def register_sensor(self, sensor_name: str, integration_name: str) -> None:
        """Register a sensor with an integration.
        
        Args:
            sensor_name: Name of the sensor.
            integration_name: Name of the integration that handles the sensor.
        """
        self._sensors[sensor_name] = integration_name
        logger.info(f"Registered sensor '{sensor_name}' with integration '{integration_name}'")
    
    def register_actuator(self, actuator_name: str, integration_name: str) -> None:
        """Register an actuator with an integration.
        
        Args:
            actuator_name: Name of the actuator.
            integration_name: Name of the integration that handles the actuator.
        """
        self._actuators[actuator_name] = integration_name
        logger.info(f"Registered actuator '{actuator_name}' with integration '{integration_name}'")
    
    def register_device_type_actions(self, device_type: str, actions: List[str]) -> None:
        """Register actions supported by a device type.
        
        Args:
            device_type: Type of device (e.g., "pump", "light").
            actions: List of actions supported by this device type.
        """
        self._device_type_actions[device_type] = actions
        logger.info(f"Registered actions for device type '{device_type}': {actions}")
        
    def register_integration_by_devices(self, integration_name: str, devices_config: Dict[str, Any]) -> None:
        """Register an integration by its device configurations.
        
        This method examines the device configurations and registers each device
        as either a sensor or actuator based on its type.
        
        Args:
            integration_name: Name of the integration.
            devices_config: Dictionary of device configurations.
        """
        sensor_types = {"temperature", "humidity", "water_level", "light_sensor", "ph", "ec", "pressure", "flow"}
        
        for device_id, device_config in devices_config.items():
            if not isinstance(device_config, dict):
                logger.error(f"Invalid device configuration for integration '{integration_name}': {device_config}")
                continue
                
            name = device_config.get("name")
            device_type = device_config.get("type")
            
            if not name or not device_type:
                logger.error(f"Invalid device configuration for integration '{integration_name}': {device_config}")
                continue
                
            # Register as sensor or actuator based on device type
            if device_type in sensor_types:
                self.register_sensor(name, integration_name)
            else:
                self.register_actuator(name, integration_name)
    
    def get_sensor_integration(self, sensor_name: str) -> Optional[str]:
        """Get the integration that handles a sensor.
        
        Args:
            sensor_name: Name of the sensor.
            
        Returns:
            Optional[str]: Name of the integration, or None if not found.
        """
        integration = self._sensors.get(sensor_name)
        if not integration:
            logger.warning(f"No integration found for sensor '{sensor_name}'")
            
        return integration
    
    def get_actuator_integration(self, actuator_name: str) -> Optional[str]:
        """Get the integration that handles an actuator.
        
        Args:
            actuator_name: Name of the actuator.
            
        Returns:
            Optional[str]: Name of the integration, or None if not found.
        """
        integration = self._actuators.get(actuator_name)
        if not integration:
            logger.warning(f"No integration found for actuator '{actuator_name}'")
            
        return integration
    
    def get_all_sensors(self) -> Dict[str, str]:
        """Get all registered sensors.
        
        Returns:
            Dict[str, str]: Map of sensor names to integration names.
        """
        return self._sensors.copy()
    
    def get_all_actuators(self) -> Dict[str, str]:
        """Get all registered actuators.
        
        Returns:
            Dict[str, str]: Map of actuator names to integration names.
        """
        return self._actuators.copy()
    
    def get_device_types(self) -> List[str]:
        """Get all unique device types registered in the system.
        
        Returns:
            List[str]: List of device type names.
        """
        return list(self._device_type_actions.keys())
    
    def get_device_actions(self, device_type: str) -> List[str]:
        """Get available actions for a specific device type.
        
        Args:
            device_type: The device type to get actions for.
            
        Returns:
            List[str]: List of supported actions for this device type.
        """
        return self._device_type_actions.get(device_type, [])
    
    def has_integration_for_action(self, action_key: str) -> bool:
        """Check if there's an integration available for a specific action.
        
        Args:
            action_key: The action key to check for, typically in format 'action_target'.
            
        Returns:
            bool: True if an integration is available, False otherwise.
        """
        # Parse the action key to get action and target
        try:
            action, target = action_key.split("_", 1)
        except ValueError:
            logger.warning(f"Invalid action key format: {action_key}")
            return False
            
        # Check if we have an actuator with this name
        return target in self._actuators
    
    def clear(self) -> None:
        """Clear the registry."""
        self._sensors.clear()
        self._actuators.clear()
        logger.info("Registry cleared")


# Create a global instance for easy imports
registry = DeviceRegistry() 
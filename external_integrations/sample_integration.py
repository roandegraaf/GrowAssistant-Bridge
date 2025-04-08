"""
Sample Integration Template.

This is a template for creating new integrations for the GrowAssistant Bridge.
Copy this file and modify it to create your own integration.

Your integration must:
1. Import the Integration base class and register_integration decorator
2. Create a class that inherits from Integration
3. Implement all required abstract methods
4. Register your class with the @register_integration decorator

To install your integration:
1. Save your Python file in the 'external_integrations' directory
2. Add configuration for your integration in config.yaml
3. Restart the application
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Generator

# Import the Integration base class and register_integration decorator
from app.integrations import Integration, register_integration

# Set up logging - always use this to provide diagnostic information
logger = logging.getLogger(__name__)


@register_integration  # <-- THIS DECORATOR IS REQUIRED - it registers your class with the system
class SampleIntegration(Integration):
    """Sample integration implementation.
    
    This is a sample integration that can be used as a template for creating new integrations.
    It simulates a device that generates random data and accepts commands.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the sample integration.
        
        Args:
            config: Configuration dictionary for this integration.
        """
        # IMPORTANT: Always call the parent class __init__
        super().__init__(config)
        
        # ALWAYS check if the integration is enabled
        if not self.config.get("enabled", False):
            logger.info("Sample Integration is disabled in configuration.")
            return
        
        # PATTERN: Parse configuration to set up devices/endpoints
        self.devices = {}
        devices_config = self.config.get("devices", {})
        
        # Process each device in the configuration
        for device_id, device_config in devices_config.items():
            # PATTERN: Validate configuration entries
            if not isinstance(device_config, dict):
                logger.error(f"Invalid device configuration: {device_config}")
                continue
                
            name = device_config.get("name")
            device_type = device_config.get("type")
            
            if not name or not device_type:
                logger.error(f"Invalid device configuration: {device_config}")
                continue
                
            # PATTERN: Store device information
            self.devices[name] = {
                "name": name,
                "type": device_type,
                "value": 0.0,
                "last_updated": time.time()
            }
            
        logger.info(f"Sample Integration initialized with {len(self.devices)} devices")
        
        # PATTERN: You can add custom instance variables here
        self.update_task = None  # Will hold the background task
        
    async def connect(self) -> bool:
        """Connect to the devices/service.
        
        IMPLEMENT THIS METHOD: Initialize connections to hardware or services
        
        Returns:
            bool: True if connection was successful, False otherwise.
        """
        # PATTERN: Check if enabled again
        if not self.config.get("enabled", False):
            return False
            
        try:
            # PATTERN: Simulate connection or connect to real hardware/service
            logger.info("Sample Integration connected")
            
            # PATTERN: Start a background task to update device values
            # This is a common pattern for devices that need polling
            self.update_task = asyncio.create_task(self._update_device_values())
            
            return True
                
        except Exception as e:
            # IMPORTANT: Always handle exceptions and log them
            logger.error(f"Failed to connect to Sample Integration: {e}")
            return False
            
    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data/command to a device.
        
        IMPLEMENT THIS METHOD: Send commands to your device/service
        
        Args:
            data: Data to send, should include:
                - device: The device name
                - value: The value to set
                
        Returns:
            bool: True if send was successful, False otherwise.
        """
        # PATTERN: Extract and validate required data
        device_name = data.get("device")
        value = data.get("value")
        
        if not device_name or value is None:
            logger.error(f"Invalid data for Sample Integration: {data}")
            return False
            
        if device_name not in self.devices:
            logger.error(f"Device not found: {device_name}")
            return False
            
        try:
            # PATTERN: Update internal state and send to hardware if applicable
            self.devices[device_name]["value"] = value
            self.devices[device_name]["last_updated"] = time.time()
            
            # PATTERN: For real devices, you would send the command to the device here
            # Example: self._send_to_hardware(device_name, value)
            
            logger.debug(f"Set {device_name} to {value}")
            return True
                
        except Exception as e:
            logger.error(f"Failed to send data to {device_name}: {e}")
            return False
            
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from the devices.
        
        IMPLEMENT THIS METHOD: Retrieve data from your device/service
        
        Yields:
            Dict[str, Any]: Data received from the devices.
        """
        # PATTERN: Return current values for all devices
        # This method is called periodically by the main application
        for name, device in self.devices.items():
            yield {
                "device": name,
                "type": device["type"],
                "value": device["value"],
                "timestamp": device["last_updated"]
            }
                
    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data/state for all devices.
        
        IMPLEMENT THIS METHOD: Return the current state of all devices
        
        Returns:
            Dict[str, Any]: Dictionary mapping device names to their current values/states.
        """
        # PATTERN: Return a dictionary with device name as key
        return {name: {
            "type": device["type"],
            "value": device["value"],
            "timestamp": device["last_updated"]
        } for name, device in self.devices.items()}
    
    async def _update_device_values(self):
        """Background task to update device values.
        
        This simulates device activity. In a real integration, this would read from hardware.
        """
        import random
        
        # Get configuration parameter
        update_interval = self.config.get("update_interval", 60)  # seconds
        
        # PATTERN: Infinite loop for continuous updates
        while True:
            try:
                for name, device in self.devices.items():
                    # Only update sensor devices (not actuators)
                    if device["type"] in ["temperature", "humidity", "light"]:
                        # Generate a random value (simulate sensor reading)
                        if device["type"] == "temperature":
                            device["value"] = round(random.uniform(18.0, 26.0), 1)
                        elif device["type"] == "humidity":
                            device["value"] = round(random.uniform(40.0, 80.0), 1)
                        elif device["type"] == "light":
                            device["value"] = round(random.uniform(0.0, 100.0), 1)
                            
                        device["last_updated"] = time.time()
                        
                # PATTERN: Sleep between updates
                await asyncio.sleep(update_interval)
            except asyncio.CancelledError:
                # Handle cancellation gracefully
                logger.info("Device update task was cancelled")
                break
            except Exception as e:
                # Handle other exceptions
                logger.error(f"Error in device update task: {e}")
                await asyncio.sleep(5)  # Wait a bit before retrying
    
    async def disconnect(self):
        """Disconnect from the devices/service and clean up resources.
        
        IMPLEMENT THIS METHOD: Clean up resources when shutting down
        """
        # PATTERN: Cancel any background tasks
        if self.update_task:
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                pass
            
        # PATTERN: Close any connections, cleanup hardware, etc.
        logger.debug("Sample Integration disconnected") 
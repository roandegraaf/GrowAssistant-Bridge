"""
DHT Sensor Integration.

This integration provides support for DHT11 and DHT22 temperature/humidity sensors
connected to GPIO pins.

Dependencies:
- Adafruit_DHT: `pip install Adafruit_DHT`

Configuration:
```yaml
integrations:
  dht:
    enabled: true
    update_interval: 60  # seconds
    devices:
      '0':
        name: greenhouse_climate
        type: temperature  # This device will be registered with both temp & humidity
        sensor_type: DHT22  # DHT22 or DHT11
        pin: 4  # GPIO pin number
```
"""

import asyncio
import logging
import time
from typing import Any, Dict, Generator

try:
    import Adafruit_DHT
    DHT_AVAILABLE = True
except ImportError:
    DHT_AVAILABLE = False

# Import the Integration base class and register_integration decorator
from app.integrations import Integration, register_integration

# Set up logging
logger = logging.getLogger(__name__)


@register_integration
class DHTIntegration(Integration):
    """Integration for DHT temperature/humidity sensors."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the DHT sensor integration.
        
        Args:
            config: Configuration dictionary for this integration.
        """
        super().__init__(config)
        
        # Check if DHT library is available
        if not DHT_AVAILABLE:
            logger.error("Adafruit_DHT library not available. Please install with: pip install Adafruit_DHT")
            return
        
        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("DHT Sensor Integration is disabled in configuration.")
            return
        
        # Parse configuration
        self.devices = {}
        self.update_task = None
        self.update_interval = self.config.get("update_interval", 60)  # seconds
        devices_config = self.config.get("devices", {})
        
        # Process each device in the configuration
        for device_id, device_config in devices_config.items():
            if not isinstance(device_config, dict):
                logger.error(f"Invalid device configuration: {device_config}")
                continue
                
            name = device_config.get("name")
            # The type will be temperature, but we'll also report humidity
            device_type = device_config.get("type")
            sensor_type = device_config.get("sensor_type", "DHT22")
            pin = device_config.get("pin")
            
            if not name or not device_type or pin is None:
                logger.error(f"Invalid device configuration: {device_config}")
                continue
                
            # Determine the DHT sensor model
            if sensor_type.upper() == "DHT11":
                dht_model = Adafruit_DHT.DHT11
            else:
                dht_model = Adafruit_DHT.DHT22
            
            self.devices[name] = {
                "name": name,
                "type": device_type,  # Main type (temperature)
                "pin": pin,
                "model": dht_model,
                "temperature": 0.0,
                "humidity": 0.0,
                "last_read": 0,
                "last_success": 0
            }
            
        logger.info(f"DHT Sensor Integration initialized with {len(self.devices)} devices")
        
    async def connect(self) -> bool:
        """Connect to the DHT sensors.
        
        Returns:
            bool: True if initialization was successful, False otherwise.
        """
        if not DHT_AVAILABLE:
            logger.error("Adafruit_DHT library not available")
            return False
            
        if not self.config.get("enabled", False):
            return False
            
        if not self.devices:
            logger.error("No DHT sensors configured")
            return False
            
        try:
            # Start a background task to read sensors
            self.update_task = asyncio.create_task(self._update_sensor_values())
            
            logger.info("DHT Sensor Integration connected")
            return True
                
        except Exception as e:
            logger.error(f"Failed to initialize DHT Sensor Integration: {e}")
            return False
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data to DHT sensors.
        
        Note: DHT sensors are read-only, so this method always returns False.
        
        Args:
            data: Data to send (ignored).
                
        Returns:
            bool: Always False for DHT sensors (read-only).
        """
        logger.warning("DHT sensors are read-only and do not accept commands")
        return False
    
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from the DHT sensors.
        
        Yields:
            Dict[str, Any]: Data received from the sensors.
        """
        for name, device in self.devices.items():
            # Only yield data if we've successfully read from the sensor
            if device["last_success"] > 0:
                # Yield temperature data
                yield {
                    "device": name,
                    "type": "temperature",
                    "value": device["temperature"],
                    "timestamp": device["last_success"]
                }
                
                # Yield humidity data (with a modified device name)
                yield {
                    "device": f"{name}_humidity",
                    "type": "humidity",
                    "value": device["humidity"],
                    "timestamp": device["last_success"]
                }
    
    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data for all DHT sensors.
        
        Returns:
            Dict[str, Any]: Dictionary mapping device names to current values.
        """
        result = {}
        
        for name, device in self.devices.items():
            if device["last_success"] > 0:
                # Add temperature data
                result[name] = {
                    "type": "temperature",
                    "value": device["temperature"],
                    "timestamp": device["last_success"]
                }
                
                # Add humidity data
                result[f"{name}_humidity"] = {
                    "type": "humidity",
                    "value": device["humidity"],
                    "timestamp": device["last_success"]
                }
                
        return result
    
    async def _update_sensor_values(self):
        """Background task to read DHT sensor values periodically."""
        while True:
            for name, device in self.devices.items():
                try:
                    # Record attempt time
                    device["last_read"] = time.time()
                    
                    # Read sensor data (this is blocking, but typically quick)
                    humidity, temperature = Adafruit_DHT.read_retry(
                        device["model"], device["pin"], retries=3
                    )
                    
                    # Check if read was successful
                    if humidity is not None and temperature is not None:
                        device["temperature"] = round(temperature, 1)
                        device["humidity"] = round(humidity, 1)
                        device["last_success"] = time.time()
                        
                        logger.debug(f"DHT sensor {name} read: {temperature}°C, {humidity}%")
                    else:
                        logger.warning(f"Failed to read from DHT sensor {name}")
                        
                except Exception as e:
                    logger.error(f"Error reading DHT sensor {name}: {e}")
            
            # Sleep until next update
            await asyncio.sleep(self.update_interval)
    
    async def disconnect(self):
        """Disconnect from the DHT sensors and clean up resources."""
        if self.update_task:
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                pass
            self.update_task = None
            
        logger.debug("DHT Sensor Integration disconnected") 
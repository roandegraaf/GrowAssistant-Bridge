"""
Climate Control Integration Example.

This integration demonstrates the full climate control flow:
- Receives target settings from API via apply_settings()
- Runs control logic comparing actual vs target values
- Uses hysteresis to prevent rapid cycling
- Reports device states back to API
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from app.api_types import LogType
from app.integrations import Integration, register_integration

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@register_integration
class ClimateControlIntegration(Integration):
    """Example integration for automatic climate control.

    This integration shows how to:
    1. Receive settings from the API
    2. Implement control logic with hysteresis
    3. Report actuator states back to the API
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("Climate Control Integration is disabled in configuration.")
            return

        # Target values from API settings
        self.target_temperature: Optional[float] = None
        self.target_humidity: Optional[int] = None

        # Current sensor readings (would come from sensor integration)
        self.current_temperature: Optional[float] = None
        self.current_humidity: Optional[int] = None

        # Actuator states
        self.heater_on: bool = False
        self.fan_on: bool = False
        self.humidifier_on: bool = False
        self.dehumidifier_on: bool = False

        # Configuration
        self.hysteresis = config.get("hysteresis", 0.5)
        self.update_interval = config.get("update_interval", 30)

        # Parse devices from configuration
        self.devices = {}
        devices_config = self.config.get("devices", {})
        for device_id, device_config in devices_config.items():
            if not isinstance(device_config, dict):
                continue
            name = device_config.get("name", device_id)
            device_type = device_config.get("type", device_id)
            self.devices[name] = {
                "name": name,
                "type": device_type,
                "value": "off",
                "last_updated": time.time(),
            }

        # Control task
        self._control_task: Optional[asyncio.Task] = None

        logger.info(
            f"ClimateControlIntegration initialized with hysteresis={self.hysteresis}, {len(self.devices)} devices"
        )

    async def connect(self) -> bool:
        """Start the climate control integration."""
        if not self.config.get("enabled", False):
            return False

        logger.info("Climate Control Integration connecting...")
        # Start the control loop
        self._control_task = asyncio.create_task(self._control_loop())
        logger.info("Climate Control Integration connected and control loop started")
        return True

    async def disconnect(self):
        """Stop the climate control integration."""
        if self._control_task:
            self._control_task.cancel()
            try:
                await self._control_task
            except asyncio.CancelledError:
                pass
        logger.info("Climate Control Integration disconnected")

    async def apply_settings(self, settings: dict[str, Any]) -> bool:
        """Receive and store target values from API.

        Args:
            settings: Settings dictionary containing:
                - climate: dict with 'temperature', 'humidity', 'baseFanSpeed'

        Returns:
            bool: True if settings were applied successfully
        """
        climate = settings.get("climate", {})

        if climate.get("temperature") is not None:
            old_temp = self.target_temperature
            self.target_temperature = float(climate["temperature"])
            if old_temp != self.target_temperature:
                logger.info(
                    f"Target temperature updated: {old_temp} -> {self.target_temperature}°C"
                )

        if climate.get("humidity") is not None:
            old_humidity = self.target_humidity
            self.target_humidity = int(climate["humidity"])
            if old_humidity != self.target_humidity:
                logger.info(f"Target humidity updated: {old_humidity} -> {self.target_humidity}%")

        return True

    async def _control_loop(self):
        """Main control logic - runs continuously."""
        logger.info("Climate control loop started")

        while True:
            try:
                # Update sensor readings (in real implementation, get from sensor integration)
                await self._update_sensor_readings()

                # Temperature control with hysteresis
                if self.target_temperature is not None and self.current_temperature is not None:
                    temp_diff = self.target_temperature - self.current_temperature

                    if temp_diff > self.hysteresis and not self.heater_on:
                        await self._set_heater(True)
                        logger.info(
                            f"Heater ON: current={self.current_temperature}°C, target={self.target_temperature}°C"
                        )
                    elif temp_diff < -self.hysteresis and self.heater_on:
                        await self._set_heater(False)
                        logger.info(
                            f"Heater OFF: current={self.current_temperature}°C, target={self.target_temperature}°C"
                        )

                # Humidity control with hysteresis
                if self.target_humidity is not None and self.current_humidity is not None:
                    humidity_diff = self.target_humidity - self.current_humidity

                    if humidity_diff > 5 and not self.humidifier_on:
                        await self._set_humidifier(True)
                        logger.info(
                            f"Humidifier ON: current={self.current_humidity}%, target={self.target_humidity}%"
                        )
                    elif humidity_diff < -5 and self.humidifier_on:
                        await self._set_humidifier(False)
                        logger.info(
                            f"Humidifier OFF: current={self.current_humidity}%, target={self.target_humidity}%"
                        )

                    if humidity_diff < -5 and not self.dehumidifier_on:
                        await self._set_dehumidifier(True)
                        logger.info(
                            f"Dehumidifier ON: current={self.current_humidity}%, target={self.target_humidity}%"
                        )
                    elif humidity_diff > 5 and self.dehumidifier_on:
                        await self._set_dehumidifier(False)
                        logger.info(
                            f"Dehumidifier OFF: current={self.current_humidity}%, target={self.target_humidity}%"
                        )

                # Report device states to API
                await self._report_device_states()

                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                logger.info("Climate control loop cancelled")
                raise
            except Exception as e:
                logger.error(f"Error in climate control loop: {e}")
                await asyncio.sleep(self.update_interval)

    async def _update_sensor_readings(self):
        """Update current sensor readings.

        In a real implementation, this would:
        - Get readings from a sensor integration
        - Or read from shared state/registry

        For this example, values would be set externally.
        """
        # Placeholder - in real implementation, get from sensor integration
        pass

    async def _set_heater(self, on: bool):
        """Control heater hardware."""
        self.heater_on = on
        # Update device value in devices dict
        for name, device in self.devices.items():
            if device.get("type") == "heater":
                device["value"] = "on" if on else "off"
                device["last_updated"] = time.time()

    async def _set_humidifier(self, on: bool):
        """Control humidifier hardware."""
        self.humidifier_on = on
        for name, device in self.devices.items():
            if device.get("type") == "humidifier":
                device["value"] = "on" if on else "off"
                device["last_updated"] = time.time()

    async def _set_dehumidifier(self, on: bool):
        """Control dehumidifier hardware."""
        self.dehumidifier_on = on
        for name, device in self.devices.items():
            if device.get("type") == "dehumidifier":
                device["value"] = "on" if on else "off"
                device["last_updated"] = time.time()

    async def _set_fan(self, on: bool):
        """Control fan hardware."""
        self.fan_on = on
        for name, device in self.devices.items():
            if device.get("type") == "fan":
                device["value"] = "on" if on else "off"
                device["last_updated"] = time.time()

    async def _report_device_states(self):
        """Report current device states to the API."""
        # Report heater state
        self.log_data(
            LogType.HEATER_STATE, "on" if self.heater_on else "off", device_id="main_heater"
        )

        # Report fan state
        self.log_data(LogType.FAN_STATE, "on" if self.fan_on else "off", device_id="exhaust_fan")

        # Report humidifier state
        self.log_data(
            LogType.HUMIDIFIER_STATE,
            "on" if self.humidifier_on else "off",
            device_id="main_humidifier",
        )

        # Report dehumidifier state
        self.log_data(
            LogType.DEHUMIDIFIER_STATE,
            "on" if self.dehumidifier_on else "off",
            device_id="main_dehumidifier",
        )

    def set_sensor_readings(
        self, temperature: Optional[float] = None, humidity: Optional[int] = None
    ):
        """Set current sensor readings (called by sensor integration).

        Args:
            temperature: Current temperature in Celsius
            humidity: Current humidity percentage
        """
        if temperature is not None:
            self.current_temperature = temperature
        if humidity is not None:
            self.current_humidity = humidity

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Send data/command to devices."""
        target = data.get("target_id")
        action = data.get("action")

        if target == "heater":
            await self._set_heater(action == "on")
            return True
        elif target == "humidifier":
            await self._set_humidifier(action == "on")
            return True
        elif target == "dehumidifier":
            await self._set_dehumidifier(action == "on")
            return True

        return False

    async def receive_data(self):
        """Receive data from devices (generator)."""
        # This integration doesn't receive data directly
        return
        yield  # Make this a generator

    async def get_device_data(self) -> dict[str, Any]:
        """Get current state of all devices."""
        # Update device values based on current states
        for name, device in self.devices.items():
            device_type = device.get("type")
            if device_type == "heater":
                device["value"] = "on" if self.heater_on else "off"
            elif device_type == "fan":
                device["value"] = "on" if self.fan_on else "off"
            elif device_type == "humidifier":
                device["value"] = "on" if self.humidifier_on else "off"
            elif device_type == "dehumidifier":
                device["value"] = "on" if self.dehumidifier_on else "off"
            device["last_updated"] = time.time()

        # Return in the format the dashboard expects
        return {
            name: {
                "type": device["type"],
                "value": device["value"],
                "timestamp": device["last_updated"],
            }
            for name, device in self.devices.items()
        }

    @classmethod
    def get_config_key(cls) -> str:
        """Return the config key for this integration."""
        return "climatecontrol"

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register this integration's capabilities with the device registry."""
        for name, device in self.devices.items():
            registry.register_actuator(
                actuator_name=name,
                integration_name=self.name,
                domain="climate",
                device_type=device.get("type"),
            )
        logger.info(f"Registered {len(self.devices)} climate control devices with registry")

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command on a target device."""
        # Find device by name
        device = None
        for name, dev in self.devices.items():
            if name == target_id:
                device = dev
                break

        if not device:
            logger.error(f"Unknown device: {target_id}")
            return False

        device_type = device.get("type")
        on = action.lower() == "on"

        if device_type == "heater":
            await self._set_heater(on)
        elif device_type == "fan":
            await self._set_fan(on)
        elif device_type == "humidifier":
            await self._set_humidifier(on)
        elif device_type == "dehumidifier":
            await self._set_dehumidifier(on)
        else:
            logger.warning(f"Unknown device type: {device_type}")
            return False

        logger.info(f"Executed command: {target_id} -> {action}")
        return True

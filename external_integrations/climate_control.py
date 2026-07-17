"""
Climate Control Integration Example.

This integration demonstrates the full climate control flow:
- Receives target settings via apply_settings()
- Runs control logic comparing actual vs target values
- Uses hysteresis to prevent rapid cycling
- Surfaces actuator states via get_device_data()
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from app.integrations import Integration, register_integration

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@register_integration
class ClimateControlIntegration(Integration):
    """Example integration for automatic climate control.

    This integration shows how to:
    1. Receive settings via apply_settings()
    2. Implement control logic with hysteresis
    3. Surface actuator states via get_device_data()
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

        # Current sensor readings, pushed by the data-collection loop via
        # on_telemetry() from whichever integration owns the configured
        # sensor entities.
        self.current_temperature: Optional[float] = None
        self.current_humidity: Optional[float] = None

        # Entity ids (`<domain>.<name>`) of the sensors this controller
        # follows, e.g. `simulator.tent_temperature` or `esphome.tent_dht`.
        self.temperature_entity: Optional[str] = config.get("temperature_entity")
        self.humidity_entity: Optional[str] = config.get("humidity_entity")

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

        if not self.temperature_entity and not self.humidity_entity:
            logger.warning(
                "Climate control has no temperature_entity/humidity_entity configured — "
                "the autonomous loop will idle until sensor entities are set"
            )

        while True:
            try:
                # Current readings arrive via on_telemetry(); the loop just
                # compares them against the targets.

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

                # Current device states surface to the app via get_device_data().
                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                logger.info("Climate control loop cancelled")
                raise
            except Exception as e:
                logger.error(f"Error in climate control loop: {e}")
                await asyncio.sleep(self.update_interval)

    async def on_telemetry(self, entity_id: str, value: Any) -> None:
        """Track the configured sensor entities from collected telemetry.

        The data-collection loop fans every joined sample out here; samples
        whose entity_id matches ``temperature_entity`` / ``humidity_entity``
        update the current readings the control loop acts on. Non-numeric
        values are ignored.
        """
        if entity_id == self.temperature_entity:
            try:
                self.current_temperature = float(value)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring non-numeric temperature reading: {value!r}")
        if entity_id == self.humidity_entity:
            try:
                self.current_humidity = float(value)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring non-numeric humidity reading: {value!r}")

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
        """Yield each actuator's current on/off state.

        The data-collection loop is the only path from an integration to app
        telemetry AND the automation engine's state store, so actuator states
        must surface here (``get_device_data()`` is never called by the
        collection loop). Each item carries an explicit dotted ``entity_id``
        matching the ``domain="climate"`` registry override — the fallback
        class-name derivation would produce ``climatecontrol.<name>`` and the
        samples would never join their manifest entity.
        """
        states = {
            "heater": self.heater_on,
            "fan": self.fan_on,
            "humidifier": self.humidifier_on,
            "dehumidifier": self.dehumidifier_on,
        }
        for name, device in self.devices.items():
            on = states.get(device.get("type"))
            if on is None:
                continue
            yield self.telemetry_sample(name, "on" if on else "off", domain="climate")

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

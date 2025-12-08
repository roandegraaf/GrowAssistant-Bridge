"""
Sample Integration Template.

This is a template for creating new integrations for the GrowAssistant Bridge.
Copy this file and modify it to create your own integration.

Your integration must:
1. Import the Integration base class and register_integration decorator
2. Create a class that inherits from Integration
3. Implement all required abstract methods
4. Register your class with the @register_integration decorator
5. Implement register_capabilities() for self-registration (NEW!)

To install your integration:
1. Save your Python file in the 'external_integrations' directory
2. Add configuration for your integration in config.yaml
3. Restart the application

NEW SELF-REGISTRATION PATTERN:
- Integrations now self-register their devices with the registry
- No changes to main.py are needed when adding new integrations
- Override get_config_key() if you want a custom config key
- Override register_capabilities() to register your sensors/actuators
- Override execute_command() for custom command handling
"""

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any, Dict, Generator

# Import API types
from app.api_types import ActionType, LogType, ProblemStatus, ProblemType

# Import the Integration base class and register_integration decorator
from app.integrations import Integration, register_integration

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

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
        for _device_id, device_config in devices_config.items():
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
                "last_updated": time.time(),
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

            # Register action handlers for different action types supported by the API
            self.register_action_handler(ActionType.TEMPERATURE, self.handle_temperature_action)
            self.register_action_handler(ActionType.HUMIDITY, self.handle_humidity_action)
            self.register_action_handler(ActionType.LIGHT, self.handle_light_action)
            self.register_action_handler(ActionType.FAN, self.handle_fan_action)
            self.register_action_handler(ActionType.TANK_ML, self.handle_tank_action)
            self.register_action_handler(ActionType.PH_VALUE, self.handle_ph_value_action)
            self.register_action_handler(ActionType.PH_ML, self.handle_ph_ml_action)
            self.register_action_handler(ActionType.SUPPLEMENT_ML, self.handle_supplement_action)

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
            # Convert to the new data log format based on device type
            if device["type"] == "temperature":
                self.log_data(LogType.TEMPERATURE, device["value"])

                # Check if temperature is out of range and report a problem if needed
                if device["value"] < 18.0 or device["value"] > 30.0:
                    self.report_problem(
                        ProblemType.TEMPERATURE,
                        ProblemStatus.RANGE,
                        f"Temperature out of range: {device['value']}°C",
                        priority=50,
                        user_can_resolve=True,
                    )

            elif device["type"] == "humidity":
                self.log_data(LogType.HUMIDITY, device["value"])

                # Check if humidity is out of range and report a problem if needed
                if device["value"] < 30.0 or device["value"] > 70.0:
                    self.report_problem(
                        ProblemType.HUMIDITY,
                        ProblemStatus.RANGE,
                        f"Humidity out of range: {device['value']}%",
                        priority=40,
                        user_can_resolve=True,
                    )

            elif device["type"] == "light":
                self.log_data(LogType.LIGHT, device["value"])

            # For compatibility with existing code, still yield the legacy format
            yield {
                "device": name,
                "type": device["type"],
                "value": device["value"],
                "timestamp": device["last_updated"],
            }

    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data/state for all devices.

        IMPLEMENT THIS METHOD: Return the current state of all devices

        Returns:
            Dict[str, Any]: Dictionary mapping device names to their current values/states.
        """
        # PATTERN: Return a dictionary with device name as key
        return {
            name: {
                "type": device["type"],
                "value": device["value"],
                "timestamp": device["last_updated"],
            }
            for name, device in self.devices.items()
        }

    async def _update_device_values(self):
        """Background task to update device values.

        This simulates device activity. In a real integration, this would read from hardware.
        """
        # Get configuration parameter
        update_interval = self.config.get("update_interval", 60)  # seconds

        # PATTERN: Infinite loop for continuous updates
        while True:
            try:
                for _name, device in self.devices.items():
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

    # Implement handlers for each action type
    async def handle_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle an action from the API.

        This method dispatches to the appropriate handler based on action type.

        Args:
            action_data: Action data from the API

        Returns:
            bool: True if action was handled successfully
        """
        action_type = action_data.get("type")

        # Map action type to handler
        handlers = {
            ActionType.TEMPERATURE.value: self.handle_temperature_action,
            ActionType.HUMIDITY.value: self.handle_humidity_action,
            ActionType.LIGHT.value: self.handle_light_action,
            ActionType.FAN.value: self.handle_fan_action,
            ActionType.TANK_ML.value: self.handle_tank_action,
            ActionType.PH_VALUE.value: self.handle_ph_value_action,
            ActionType.PH_ML.value: self.handle_ph_ml_action,
            ActionType.SUPPLEMENT_ML.value: self.handle_supplement_action,
        }

        if action_type in handlers:
            return await handlers[action_type](action_data)
        else:
            logger.warning(f"No handler for action type: {action_type}")
            return False

    async def handle_temperature_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle temperature action.

        Args:
            action_data: Action data from API

        Returns:
            bool: True if handled successfully
        """
        try:
            # Extract action details
            action_data.get("id")
            value = float(action_data.get("value", 0))

            # Find temperature controller device
            temp_controller = None
            for name, device in self.devices.items():
                if device["type"] == "temperature":
                    temp_controller = name
                    break

            if temp_controller:
                # Send command to device
                await self.send_data({"device": temp_controller, "value": value})

                # Log success
                logger.info(f"Set temperature to {value}")
                return True
            else:
                logger.warning("No temperature controller found")
                return False

        except Exception as e:
            logger.error(f"Error handling temperature action: {e}")
            return False

    async def handle_humidity_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle humidity action.

        Args:
            action_data: Action data from API

        Returns:
            bool: True if handled successfully
        """
        try:
            # Extract action details
            action_data.get("id")
            value = float(action_data.get("value", 0))

            # Find humidity controller device
            humidity_controller = None
            for name, device in self.devices.items():
                if device["type"] == "humidity":
                    humidity_controller = name
                    break

            if humidity_controller:
                # Send command to device
                await self.send_data({"device": humidity_controller, "value": value})

                # Log success
                logger.info(f"Set humidity to {value}")
                return True
            else:
                logger.warning("No humidity controller found")
                return False

        except Exception as e:
            logger.error(f"Error handling humidity action: {e}")
            return False

    async def handle_light_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle light action.

        The API sends simple light on/off commands via actions.
        Light schedules (day/night) are configured via settings, not actions.

        Args:
            action_data: Action data from API with 'value' (on/off state)

        Returns:
            bool: True if handled successfully
        """
        try:
            # Extract action details
            action_data.get("id")
            value = action_data.get("value")  # Typically "on" or "off", or a state string

            # Find light controller device
            light_controller = None
            for name, device in self.devices.items():
                if device["type"] == "light" or device["type"] == "light_switch":
                    light_controller = name
                    break

            if light_controller:
                # Convert value to appropriate format for your hardware
                # For example, "on" -> 1, "off" -> 0
                hardware_value = value
                if isinstance(value, str):
                    if value.lower() == "on":
                        hardware_value = 1
                    elif value.lower() == "off":
                        hardware_value = 0

                # Send command to device
                await self.send_data({"device": light_controller, "value": hardware_value})

                # Log success
                logger.info(f"Set light to {value}")
                return True
            else:
                logger.warning("No light controller found")
                return False

        except Exception as e:
            logger.error(f"Error handling light action: {e}")
            return False

    async def handle_fan_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle fan action.

        Args:
            action_data: Action data from API

        Returns:
            bool: True if handled successfully
        """
        try:
            # Extract action details
            action_data.get("id")
            value = float(action_data.get("value", 0))

            # Find fan controller device
            fan_controller = None
            for name, device in self.devices.items():
                if device["type"] == "fan":
                    fan_controller = name
                    break

            if fan_controller:
                # Send command to device
                await self.send_data({"device": fan_controller, "value": value})

                # Log success
                logger.info(f"Set fan to {value}")
                return True
            else:
                logger.warning("No fan controller found")
                return False

        except Exception as e:
            logger.error(f"Error handling fan action: {e}")
            return False

    async def handle_tank_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle tank water amount action.

        Args:
            action_data: Action data from API with 'value' (amount in ML)

        Returns:
            bool: True if handled successfully
        """
        try:
            action_data.get("id")
            value = float(action_data.get("value", 0))
            action_data.get("pumpNumber")

            logger.info(f"Setting tank water amount to {value}ML")

            # In a real implementation, you would:
            # - Update your tank monitoring system
            # - Possibly trigger water level alerts
            # - Log the new tank capacity

            return True

        except Exception as e:
            logger.error(f"Error handling tank action: {e}")
            return False

    async def handle_ph_value_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle pH value target action.

        Args:
            action_data: Action data from API with 'value' (target pH)

        Returns:
            bool: True if handled successfully
        """
        try:
            action_data.get("id")
            value = float(action_data.get("value", 0))
            pump_number = action_data.get("pumpNumber")

            logger.info(f"Setting target pH to {value} for pump {pump_number}")

            # In a real implementation, you would:
            # - Update your pH controller with the new target
            # - Enable automatic pH adjustment
            # - Configure the dosing pump

            return True

        except Exception as e:
            logger.error(f"Error handling pH value action: {e}")
            return False

    async def handle_ph_ml_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle pH adjustment volume action.

        Args:
            action_data: Action data from API with 'value' (volume in ML)
                        and 'pumpNumber' (which pump to use)

        Returns:
            bool: True if handled successfully
        """
        try:
            action_data.get("id")
            value = float(action_data.get("value", 0))
            pump_number = action_data.get("pumpNumber")

            logger.info(f"Dosing {value}ML of pH adjuster from pump {pump_number}")

            # In a real implementation, you would:
            # - Activate the specified dosing pump
            # - Dispense the specified volume
            # - Log the dosing event for tracking
            # - Monitor pH changes

            # Log the pump action with pump number
            self.log_data(LogType.PH_ML, value, pump_num=pump_number)

            return True

        except Exception as e:
            logger.error(f"Error handling pH ML action: {e}")
            return False

    async def handle_supplement_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle nutrient supplement volume action.

        Args:
            action_data: Action data from API with 'value' (volume in ML)
                        and 'pumpNumber' (which pump to use)

        Returns:
            bool: True if handled successfully
        """
        try:
            action_data.get("id")
            value = float(action_data.get("value", 0))
            pump_number = action_data.get("pumpNumber")

            logger.info(f"Dosing {value}ML of nutrients from pump {pump_number}")

            # In a real implementation, you would:
            # - Activate the specified nutrient pump
            # - Dispense the specified volume
            # - Log the feeding event
            # - Update nutrient tracking

            # Log the supplement action with pump number
            self.log_data(LogType.SUPPLEMENT_ML, value, pump_num=pump_number)

            return True

        except Exception as e:
            logger.error(f"Error handling supplement action: {e}")
            return False

    async def apply_settings(self, settings: Dict[str, Any]) -> bool:
        """Apply settings received from the API.

        This demonstrates how to handle settings updates from the API.
        Integrations can implement this to apply light schedules, climate
        settings, pump schedules, etc.

        Args:
            settings: Settings dictionary containing:
                - rdh_mode: bool
                - status: str
                - light: dict with 'day' and 'night' settings
                - climate: dict with 'temperature', 'humidity', 'baseFanSpeed'
                - tank: dict with 'waters', 'ph', 'amountML'

        Returns:
            bool: True if settings were applied successfully
        """
        try:
            logger.info(f"Applying settings from API: {settings}")

            # Apply light settings
            # The API sends light schedules as strings (e.g., "06:00-18:00" or "auto")
            light_settings = settings.get("light", {})
            if light_settings:
                day_setting = light_settings.get("day")  # e.g., "06:00-18:00" or schedule string
                night_setting = light_settings.get("night")  # e.g., "off" or night schedule

                logger.info(f"Light settings - Day: {day_setting}, Night: {night_setting}")

                # In a real implementation, you would:
                # - Parse the schedule strings
                # - Set up timers or cron jobs for light control
                # - Update your light controller with the new schedule
                # Example: Parse "06:00-18:00" to turn lights on at 6am and off at 6pm

                for name, device in self.devices.items():
                    if device["type"] == "light" or device["type"] == "light_switch":
                        logger.info(
                            f"Configuring light schedule for {name}: Day={day_setting}, Night={night_setting}"
                        )
                        # Here you would implement actual scheduling logic

            # Apply climate settings
            climate_settings = settings.get("climate", {})
            if climate_settings:
                target_temp = climate_settings.get("temperature")
                target_humidity = climate_settings.get("humidity")
                fan_speed = climate_settings.get("baseFanSpeed")

                logger.info(
                    f"Climate settings - Temp: {target_temp}, Humidity: {target_humidity}, Fan: {fan_speed}"
                )

                # Apply temperature setting
                if target_temp is not None:
                    for name, device in self.devices.items():
                        if device["type"] == "temperature":
                            await self.send_data({"device": name, "value": target_temp})

                # Apply humidity setting
                if target_humidity is not None:
                    for name, device in self.devices.items():
                        if device["type"] == "humidity":
                            await self.send_data({"device": name, "value": target_humidity})

                # Apply fan speed
                if fan_speed is not None:
                    for name, device in self.devices.items():
                        if device["type"] == "fan":
                            await self.send_data({"device": name, "value": fan_speed})

            # Apply tank/water settings
            tank_settings = settings.get("tank", {})
            if tank_settings:
                waters = tank_settings.get("waters", [])  # Array of pump configurations
                ph_setting = tank_settings.get("ph", {})  # pH target and pump number
                tank_amount = tank_settings.get("amountML")  # Total tank capacity

                logger.info(
                    f"Tank settings - {len(waters)} pump(s) configured, pH: {ph_setting}, Tank capacity: {tank_amount}ML"
                )

                # Process water schedules for each pump
                for water in waters:
                    pump_num = water.get("pumpNum")
                    schedules = water.get("waterSchedules", [])

                    logger.info(f"Configuring pump {pump_num} with {len(schedules)} schedule(s)")

                    # Each schedule contains:
                    # - waterAmountML: Amount to dispense
                    # - startTime: When to start (time string)
                    # - endTime: When to end (time string)
                    # - scheduleType: Type of schedule
                    for schedule in schedules:
                        amount = schedule.get("waterAmountML")
                        start = schedule.get("startTime")
                        end = schedule.get("endTime")
                        schedule_type = schedule.get("scheduleType")

                        logger.info(
                            f"  Pump {pump_num}: {amount}ML from {start} to {end} ({schedule_type})"
                        )

                        # In a real implementation, you would:
                        # - Parse the time strings
                        # - Set up timers or cron jobs for watering
                        # - Configure the pump controller
                        # - Track water usage

                # Apply pH settings
                if ph_setting:
                    target_ph = ph_setting.get("ph")
                    ph_pump_num = ph_setting.get("pumpNum")
                    if target_ph and ph_pump_num:
                        logger.info(f"Setting pH target to {target_ph} using pump {ph_pump_num}")
                        # Configure pH controller with target and pump number

            logger.info("Settings applied successfully")
            return True

        except Exception as e:
            logger.error(f"Error applying settings: {e}")
            return False

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

    # =========================================================================
    # NEW: Self-Registration Methods (Home Assistant-style modularity)
    # =========================================================================

    @classmethod
    def get_config_key(cls) -> str:
        """Return the config key for this integration.

        This is the key used in config.yaml under 'integrations:'.
        Default implementation removes 'Integration' suffix and lowercases.

        Override this if you want a custom config key.

        Returns:
            str: The configuration key (e.g., "sample").
        """
        # Default: SampleIntegration -> "sample"
        return "sample"

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register this integration's capabilities with the device registry.

        NEW PATTERN: This method is called after connect() succeeds.
        Register all your sensors and actuators here.

        This replaces the old hardcoded _register_*_capabilities() methods
        that were in main.py. Now each integration handles its own registration!

        Args:
            registry: The DeviceRegistry instance to register with.
        """
        sensor_types = {"temperature", "humidity", "light", "ph", "ec", "water_level"}

        for name, device in self.devices.items():
            device_type = device.get("type")

            if device_type in sensor_types:
                registry.register_sensor(
                    sensor_name=name,
                    integration_name=self.name,
                    domain="sample",  # Use your integration's domain
                    device_type=device_type,
                )
            else:
                registry.register_actuator(
                    actuator_name=name,
                    integration_name=self.name,
                    domain="sample",
                    device_type=device_type,
                )

        logger.info(f"Registered {len(self.devices)} devices with registry")

    async def execute_command(self, target_id: str, action: str, payload: Dict[str, Any]) -> bool:
        """Execute a command on a target device.

        NEW PATTERN: This is the unified command interface that replaces
        the old signature mismatch bug. Commands from the API are routed
        through this method.

        Args:
            target_id: The target device name.
            action: The action to perform (e.g., "on", "off", "set").
            payload: Additional command parameters.

        Returns:
            bool: True if command executed successfully.
        """
        if target_id not in self.devices:
            logger.error(f"Unknown device: {target_id}")
            return False

        # Handle common actions
        if action.lower() in ("on", "off"):
            value = 1 if action.lower() == "on" else 0
            return await self.send_data(
                {
                    "device": target_id,
                    "value": value,
                }
            )
        elif action.lower() == "set":
            value = payload.get("value")
            if value is not None:
                return await self.send_data(
                    {
                        "device": target_id,
                        "value": value,
                    }
                )
        else:
            # Pass through for custom actions
            return await self.send_data(
                {
                    "device": target_id,
                    "action": action,
                    **payload,
                }
            )

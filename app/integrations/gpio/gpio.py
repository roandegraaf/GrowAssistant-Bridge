"""
GPIO Integration Implementation.

This module provides the GPIOIntegration class for interacting with GPIO pins.
It's designed to work with Raspberry Pi but gracefully degrades when not on a Pi.
"""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from app.integrations import Integration, register_integration
from app.schemas.config_schemas import GPIOIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)

# Try to import RPi.GPIO, but don't fail if not available
try:
    import RPi.GPIO as GPIO

    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("RPi.GPIO not available. GPIO Integration will run in dummy mode.")


@register_integration
class GPIOIntegration(Integration):
    """Integration for GPIO pins on Raspberry Pi."""

    CONFIG_SCHEMA = GPIOIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        """Initialize the GPIO integration.

        Args:
            config: Configuration dictionary for GPIO integration.
        """
        super().__init__(config)
        self.pins: dict[str, dict[str, Any]] = {}
        self.initialized = False

        if not self.config.get("enabled", False):
            logger.info("GPIO Integration is disabled in configuration.")
            return

        if not GPIO_AVAILABLE:
            logger.warning("GPIO Integration enabled in config but RPi.GPIO not available.")
            return

        pin_configs = self.config.get("pins", [])
        if not pin_configs:
            logger.warning("No GPIO pins configured.")
            return

        for pin_config in pin_configs:
            name = pin_config.get("name")
            pin = pin_config.get("pin")

            if not name or pin is None:
                logger.error(f"Invalid pin configuration: {pin_config}")
                continue

            self.pins[name] = {
                "pin": pin,
                "direction": pin_config.get("direction", "IN"),
                "initial": pin_config.get("initial", "LOW"),
                "pull_up_down": pin_config.get("pull_up_down"),
            }

        logger.info(
            f"GPIO Integration initialized with {len(self.pins)} pins: {', '.join(self.pins.keys())}"
        )

    async def connect(self) -> bool:
        """Initialize the GPIO library and set up configured pins.

        Returns:
            bool: True if initialization was successful, False otherwise.
        """
        if not self.config.get("enabled", False) or not GPIO_AVAILABLE:
            return False

        pull_up_down_map = {"UP": GPIO.PUD_UP, "DOWN": GPIO.PUD_DOWN}

        try:
            GPIO.setmode(GPIO.BCM)

            for pin_config in self.pins.values():
                pin = pin_config["pin"]
                is_output = pin_config["direction"] == "OUT"
                direction = GPIO.OUT if is_output else GPIO.IN

                if is_output:
                    initial = GPIO.HIGH if pin_config["initial"] == "HIGH" else GPIO.LOW
                    GPIO.setup(pin, direction, initial=initial)
                else:
                    pull_up_down = pull_up_down_map.get(pin_config["pull_up_down"])
                    if pull_up_down is not None:
                        GPIO.setup(pin, direction, pull_up_down=pull_up_down)
                    else:
                        GPIO.setup(pin, direction)

            self.initialized = True
            logger.info("GPIO Integration connected successfully.")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize GPIO: {e}")
            return False

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Set the state of a GPIO pin.

        Args:
            data: Dictionary containing:
                - pin_name: Name of the pin to set
                - state: "HIGH" or "LOW"

        Returns:
            bool: True if the operation was successful, False otherwise.
        """
        if not self.initialized or not GPIO_AVAILABLE:
            logger.error("GPIO not initialized. Cannot send data.")
            return False

        pin_name = data.get("pin_name")
        state = data.get("state")

        if not pin_name or not state:
            logger.error(f"Invalid GPIO data: {data}")
            return False

        pin_config = self.pins.get(pin_name)
        if not pin_config:
            logger.error(f"Unknown GPIO pin: {pin_name}")
            return False

        if pin_config["direction"] != "OUT":
            logger.error(f"Cannot write to input pin: {pin_name}")
            return False

        try:
            pin = pin_config["pin"]
            gpio_state = GPIO.HIGH if state == "HIGH" else GPIO.LOW
            GPIO.output(pin, gpio_state)
            logger.debug(f"Set {pin_name} (pin {pin}) to {state}")
            return True
        except Exception as e:
            logger.error(f"Failed to set GPIO pin {pin_name}: {e}")
            return False

    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        """Read the state of all input GPIO pins.

        Yields:
            Dict[str, Any]: Data read from each input pin.
        """
        if not self.initialized or not GPIO_AVAILABLE:
            logger.error("GPIO not initialized. Cannot receive data.")
            return

        for name, pin_config in self.pins.items():
            if pin_config["direction"] != "IN":
                continue

            try:
                pin = pin_config["pin"]
                state = GPIO.input(pin)
                is_high = state == GPIO.HIGH
                yield {
                    "pin_name": name,
                    "pin": pin,
                    "state": "HIGH" if is_high else "LOW",
                    "value": 1 if is_high else 0,
                }
            except Exception as e:
                logger.error(f"Failed to read GPIO pin {name}: {e}")

    async def get_device_data(self) -> dict[str, Any]:
        """Get the current data/state for all GPIO pins.

        Returns:
            Dict[str, Any]: Dictionary mapping pin names to their current states.
        """
        if not self.initialized or not GPIO_AVAILABLE:
            return {}

        device_data = {}
        for name, pin_config in self.pins.items():
            try:
                pin = pin_config["pin"]
                if pin_config["direction"] == "IN":
                    state = GPIO.input(pin)
                    is_high = state == GPIO.HIGH
                    device_data[name] = {
                        "state": "HIGH" if is_high else "LOW",
                        "value": 1 if is_high else 0,
                    }
                else:
                    device_data[name] = {"direction": "OUT"}
            except Exception as e:
                logger.error(f"Failed to read GPIO pin {name}: {e}")
                device_data[name] = {"error": str(e)}

        return device_data

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register GPIO pin capabilities with the device registry.

        Args:
            registry: The DeviceRegistry instance to register with.
        """
        for pin_name, pin_config in self.pins.items():
            direction = pin_config.get("direction", "").upper()

            if direction == "OUT":
                registry.register_actuator(
                    actuator_name=pin_name,
                    integration_name=self.name,
                    domain="gpio",
                    device_type="gpio_output",
                )
            elif direction == "IN":
                registry.register_sensor(
                    sensor_name=pin_name,
                    integration_name=self.name,
                    domain="gpio",
                    device_type="gpio_input",
                )

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command on a GPIO pin.

        Args:
            target_id: The pin name.
            action: The action (e.g., "on", "off", "HIGH", "LOW").
            payload: Additional parameters (unused).

        Returns:
            bool: True if successful.
        """
        state_map = {"on": "HIGH", "off": "LOW", "high": "HIGH", "low": "LOW"}
        state = state_map.get(action.lower(), action.upper())
        return await self.send_data({"pin_name": target_id, "state": state})

    def __del__(self):
        """Clean up GPIO resources when the object is destroyed."""
        if GPIO_AVAILABLE and self.initialized:
            try:
                GPIO.cleanup()
                logger.debug("GPIO resources cleaned up.")
            except Exception as e:
                logger.error(f"Failed to clean up GPIO resources: {e}")

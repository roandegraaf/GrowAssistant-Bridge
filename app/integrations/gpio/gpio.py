"""
GPIO Integration Implementation.

This module provides the GPIOIntegration class for interacting with GPIO pins.
It's designed to work with Raspberry Pi but gracefully degrades when not on a Pi.
"""

import asyncio
import logging
from typing import Any, Dict, Generator, Optional

from app.integrations import Integration, register_integration

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
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the GPIO integration.
        
        Args:
            config: Configuration dictionary for GPIO integration.
        """
        super().__init__(config)
        self.pins = {}
        self.initialized = False
        
        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("GPIO Integration is disabled in configuration.")
            return
            
        # Check if GPIO is available
        if not GPIO_AVAILABLE:
            logger.warning("GPIO Integration enabled in config but RPi.GPIO not available.")
            return
            
        # Parse pin configurations
        pin_configs = self.config.get("pins", [])
        if not pin_configs:
            logger.warning("No GPIO pins configured.")
            return
            
        # Process each pin configuration
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
                "pull_up_down": pin_config.get("pull_up_down", None)
            }
            
        logger.info(f"GPIO Integration initialized with {len(self.pins)} pins: {', '.join(self.pins.keys())}")
    
    async def connect(self) -> bool:
        """Initialize the GPIO library and set up configured pins.
        
        Returns:
            bool: True if initialization was successful, False otherwise.
        """
        if not self.config.get("enabled", False) or not GPIO_AVAILABLE:
            return False
            
        try:
            # Set GPIO mode
            GPIO.setmode(GPIO.BCM)
            
            # Set up each pin
            for name, pin_config in self.pins.items():
                pin = pin_config["pin"]
                direction = GPIO.OUT if pin_config["direction"] == "OUT" else GPIO.IN
                
                # Set up input pins
                if direction == GPIO.IN:
                    pull_up_down = None
                    if pin_config["pull_up_down"] == "UP":
                        pull_up_down = GPIO.PUD_UP
                    elif pin_config["pull_up_down"] == "DOWN":
                        pull_up_down = GPIO.PUD_DOWN
                        
                    if pull_up_down is not None:
                        GPIO.setup(pin, direction, pull_up_down=pull_up_down)
                    else:
                        GPIO.setup(pin, direction)
                
                # Set up output pins
                else:
                    initial = GPIO.HIGH if pin_config["initial"] == "HIGH" else GPIO.LOW
                    GPIO.setup(pin, direction, initial=initial)
                    
            self.initialized = True
            logger.info("GPIO Integration connected successfully.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize GPIO: {e}")
            return False
            
    async def send_data(self, data: Dict[str, Any]) -> bool:
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
            
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Read the state of all input GPIO pins.
        
        Yields:
            Dict[str, Any]: Data read from each input pin.
        """
        if not self.initialized or not GPIO_AVAILABLE:
            logger.error("GPIO not initialized. Cannot receive data.")
            return
            
        for name, pin_config in self.pins.items():
            if pin_config["direction"] == "IN":
                try:
                    pin = pin_config["pin"]
                    state = GPIO.input(pin)
                    state_str = "HIGH" if state == GPIO.HIGH else "LOW"
                    yield {
                        "pin_name": name,
                        "pin": pin,
                        "state": state_str,
                        "value": 1 if state == GPIO.HIGH else 0
                    }
                except Exception as e:
                    logger.error(f"Failed to read GPIO pin {name}: {e}")
                    
    def __del__(self):
        """Clean up GPIO resources when the object is destroyed."""
        if GPIO_AVAILABLE and self.initialized:
            try:
                GPIO.cleanup()
                logger.debug("GPIO resources cleaned up.")
            except Exception as e:
                logger.error(f"Failed to clean up GPIO resources: {e}") 
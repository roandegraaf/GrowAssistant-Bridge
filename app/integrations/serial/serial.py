"""
Serial Integration Implementation.

This module provides the SerialIntegration class for interacting with serial devices.
It uses the pyserial library for serial communication.
"""

import asyncio
import json
import logging
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import serial
import serial.tools.list_ports

from app.integrations import Integration, register_integration
from app.schemas.config_schemas import SerialIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@register_integration
class SerialIntegration(Integration):
    """Integration for serial communication."""

    CONFIG_SCHEMA = SerialIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        """Initialize the Serial integration.

        Args:
            config: Configuration dictionary for Serial integration.
        """
        super().__init__(config)
        self.serial: serial.Serial | None = None
        self.serial_connected = False
        self.serial_lock = asyncio.Lock()
        self.reader_task: asyncio.Task | None = None
        self.read_buffer: list[dict[str, Any]] = []

        if not self.config.get("enabled", False):
            logger.info("Serial Integration is disabled in configuration.")
            return

        self.port = self.config.get("port")
        if not self.port:
            logger.error("No serial port specified in configuration.")
            return

        self.baudrate = self.config.get("baudrate", 9600)
        self.timeout = self.config.get("timeout", 1)

        bytesize_map = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        self.bytesize = bytesize_map.get(self.config.get("bytesize", 8), serial.EIGHTBITS)

        parity_map = {
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
            "N": serial.PARITY_NONE,
        }
        self.parity = parity_map.get(self.config.get("parity", "N"), serial.PARITY_NONE)

        stopbits_map = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }
        self.stopbits = stopbits_map.get(self.config.get("stopbits", 1), serial.STOPBITS_ONE)

        logger.info(f"Serial Integration initialized with port {self.port} at {self.baudrate} baud")

    async def connect(self) -> bool:
        """Connect to the serial device.

        Returns:
            bool: True if connection was successful, False otherwise.
        """
        if not self.config.get("enabled", False):
            return False

        try:
            available_ports = [p.device for p in serial.tools.list_ports.comports()]
            if self.port not in available_ports:
                logger.error(
                    f"Serial port {self.port} not found. "
                    f"Available ports: {', '.join(available_ports)}"
                )
                return False

            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )

            if not self.serial.is_open:
                self.serial.open()

            self.serial_connected = True
            logger.info(f"Connected to serial port {self.port}")

            self.reader_task = asyncio.create_task(self._read_serial())
            return True

        except Exception as e:
            logger.error(f"Failed to connect to serial port {self.port}: {e}")
            if self.serial and self.serial.is_open:
                self.serial.close()
            self.serial = None
            self.serial_connected = False
            return False

    async def _read_serial(self):
        """Background task to continuously read from the serial port."""
        while self.serial_connected and self.serial and self.serial.is_open:
            try:
                if not self.serial.in_waiting:
                    await asyncio.sleep(0.1)
                    continue

                async with self.serial_lock:
                    line = self.serial.readline().decode("utf-8", errors="replace").strip()

                if not line:
                    continue

                logger.debug(f"Received from serial: {line}")
                try:
                    data = json.loads(line)
                    data.setdefault("timestamp", time.time())
                except json.JSONDecodeError:
                    data = {"timestamp": time.time(), "data": line}

                self.read_buffer.append(data)

            except Exception as e:
                logger.error(f"Error reading from serial port: {e}")
                await asyncio.sleep(1)

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Send data to the serial device.

        Args:
            data: Dictionary containing:
                - payload: String or dict/list to send (dict/list will be JSON-encoded)
                - add_newline: Whether to add a newline to the end (default: True)

        Returns:
            bool: True if send was successful, False otherwise.
        """
        if not self.serial_connected or not self.serial or not self.serial.is_open:
            logger.error("Serial not connected. Cannot send data.")
            return False

        payload = data.get("payload")
        if payload is None:
            logger.error("No payload provided in serial data")
            return False

        try:
            payload_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

            if data.get("add_newline", True) and not payload_str.endswith("\n"):
                payload_str += "\n"

            async with self.serial_lock:
                bytes_written = self.serial.write(payload_str.encode("utf-8"))
                self.serial.flush()

            logger.debug(f"Sent {bytes_written} bytes to serial port: {payload_str.strip()}")
            return True

        except Exception as e:
            logger.error(f"Failed to send data to serial port: {e}")
            return False

    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        """Receive data from the serial device.

        Telemetry contract: a parsed JSON line must identify its device so the
        sample can join a registered ``serial.<name>`` entity (declare the
        devices under this integration's ``devices:`` config). Accepted line
        shapes, most explicit first:

        - ``{"entity_id": "serial.pump1", "value": …}`` — passed through.
        - ``{"device": "pump1", "value": …}`` (or ``"name"``) — mapped to
          ``serial.<device>``.

        Lines with neither (including non-JSON lines, buffered as raw
        ``data``) cannot join any entity and are skipped with a log.
        """
        if not self.serial_connected or not self.serial or not self.serial.is_open:
            logger.error("Serial not connected. Cannot receive data.")
            return

        buffer_copy = self.read_buffer.copy()
        self.read_buffer.clear()

        for data in buffer_copy:
            explicit = data.get("entity_id")
            if isinstance(explicit, str) and "." in explicit:
                yield data
                continue

            device = data.get("device") or data.get("name")
            if device:
                extras = {k: v for k, v in data.items() if k not in ("device", "name", "value")}
                yield self.telemetry_sample(
                    str(device), data.get("value"), domain="serial", **extras
                )
                continue

            logger.warning(
                f"Skipping serial line with no device identity "
                f"(expected 'entity_id' or 'device' key): {str(data)[:200]}"
            )

    async def get_device_data(self) -> dict[str, Any]:
        """Get the current data/state for the serial device.

        Returns:
            Dict[str, Any]: Dictionary containing the latest buffered data.
        """
        return {
            "connected": self.serial_connected,
            "buffer_size": len(self.read_buffer),
            "latest": self.read_buffer[-1] if self.read_buffer else None,
        }

    async def close(self):
        """Close the serial connection."""
        if self.reader_task:
            self.reader_task.cancel()
            try:
                await self.reader_task
            except asyncio.CancelledError:
                pass

        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
                logger.debug(f"Closed serial port {self.port}")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")

        self.serial_connected = False

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command via serial.

        Args:
            target_id: The target device identifier.
            action: The action to perform.
            payload: Additional command parameters.

        Returns:
            bool: True if successful.
        """
        return await self.send_data(
            {
                "payload": {"target": target_id, "action": action, **payload},
            }
        )

    def __del__(self):
        """Clean up serial resources when the object is destroyed."""
        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
                logger.debug(f"Closed serial port {self.port}")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")

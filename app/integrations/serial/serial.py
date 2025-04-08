"""
Serial Integration Implementation.

This module provides the SerialIntegration class for interacting with serial devices.
It uses the pyserial library for serial communication.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Generator, Optional

import serial
import serial.tools.list_ports

from app.integrations import Integration, register_integration

logger = logging.getLogger(__name__)


@register_integration
class SerialIntegration(Integration):
    """Integration for serial communication."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the Serial integration.
        
        Args:
            config: Configuration dictionary for Serial integration.
        """
        super().__init__(config)
        self.serial = None
        self.serial_connected = False
        self.serial_lock = asyncio.Lock()
        self.reader_task = None
        self.read_buffer = []
        
        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("Serial Integration is disabled in configuration.")
            return
            
        # Get configuration parameters
        self.port = self.config.get("port")
        if not self.port:
            logger.error("No serial port specified in configuration.")
            return
            
        self.baudrate = self.config.get("baudrate", 9600)
        self.bytesize = self.config.get("bytesize", 8)
        self.parity = self.config.get("parity", "N")
        self.stopbits = self.config.get("stopbits", 1)
        self.timeout = self.config.get("timeout", 1)
        
        # Convert bytesize to serial.EIGHTBITS, etc.
        if self.bytesize == 5:
            self.bytesize = serial.FIVEBITS
        elif self.bytesize == 6:
            self.bytesize = serial.SIXBITS
        elif self.bytesize == 7:
            self.bytesize = serial.SEVENBITS
        else:
            self.bytesize = serial.EIGHTBITS
            
        # Convert parity to serial.PARITY_NONE, etc.
        if self.parity == "E":
            self.parity = serial.PARITY_EVEN
        elif self.parity == "O":
            self.parity = serial.PARITY_ODD
        elif self.parity == "M":
            self.parity = serial.PARITY_MARK
        elif self.parity == "S":
            self.parity = serial.PARITY_SPACE
        else:
            self.parity = serial.PARITY_NONE
            
        # Convert stopbits to serial.STOPBITS_ONE, etc.
        if self.stopbits == 1.5:
            self.stopbits = serial.STOPBITS_ONE_POINT_FIVE
        elif self.stopbits == 2:
            self.stopbits = serial.STOPBITS_TWO
        else:
            self.stopbits = serial.STOPBITS_ONE
            
        logger.info(f"Serial Integration initialized with port {self.port} at {self.baudrate} baud")
        
    async def connect(self) -> bool:
        """Connect to the serial device.
        
        Returns:
            bool: True if connection was successful, False otherwise.
        """
        if not self.config.get("enabled", False):
            return False
            
        try:
            # Check if port exists
            available_ports = [p.device for p in serial.tools.list_ports.comports()]
            if self.port not in available_ports:
                logger.error(f"Serial port {self.port} not found. Available ports: {', '.join(available_ports)}")
                return False
                
            # Create serial connection
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout
            )
            
            # Open the port if it's not already open
            if not self.serial.is_open:
                self.serial.open()
                
            self.serial_connected = True
            logger.info(f"Connected to serial port {self.port}")
            
            # Start reader task
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
                # Check if there's data available to read
                if self.serial.in_waiting:
                    async with self.serial_lock:
                        line = self.serial.readline().decode('utf-8', errors='replace').strip()
                        if line:
                            logger.debug(f"Received from serial: {line}")
                            # Try to parse as JSON
                            try:
                                data = json.loads(line)
                                # Add timestamp if not present
                                if "timestamp" not in data:
                                    data["timestamp"] = time.time()
                                self.read_buffer.append(data)
                            except json.JSONDecodeError:
                                # If not JSON, store as plain text
                                self.read_buffer.append({
                                    "timestamp": time.time(),
                                    "data": line
                                })
                # Yield control to other tasks
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error reading from serial port: {e}")
                await asyncio.sleep(1)  # Longer delay after error
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
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
        add_newline = data.get("add_newline", True)
        
        if payload is None:
            logger.error("No payload provided in serial data")
            return False
            
        try:
            # Convert payload to string if it's a dict or list
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload)
            else:
                payload_str = str(payload)
                
            # Add newline if requested
            if add_newline and not payload_str.endswith('\n'):
                payload_str += '\n'
                
            # Send data
            async with self.serial_lock:
                bytes_written = self.serial.write(payload_str.encode('utf-8'))
                self.serial.flush()  # Ensure all data is written
                
            logger.debug(f"Sent {bytes_written} bytes to serial port: {payload_str.strip()}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send data to serial port: {e}")
            return False
            
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from the serial device.
        
        Yields:
            Dict[str, Any]: Data received from the serial device.
        """
        if not self.serial_connected or not self.serial or not self.serial.is_open:
            logger.error("Serial not connected. Cannot receive data.")
            return
            
        # Return all data in the buffer
        buffer_copy = self.read_buffer.copy()
        self.read_buffer = []
        
        for data in buffer_copy:
            yield data
    
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
    
    def __del__(self):
        """Clean up serial resources when the object is destroyed."""
        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
                logger.debug(f"Closed serial port {self.port}")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")
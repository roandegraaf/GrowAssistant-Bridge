"""
API Client Module.

This module provides a client for communicating with the Spring API,
handling authentication, data transmission, and command reception.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.auth import auth_manager
from app.config import config


logger = logging.getLogger(__name__)


class ApiClient:
    """Client for interacting with the GrowAssistant Spring API.
    
    This class provides methods for sending data to the API and receiving commands.
    It handles authentication, error handling, and retry logic.
    
    Attributes:
        _instance: Singleton instance of the ApiClient.
        _client: HTTP client for making requests.
        _base_url: Base URL of the API.
    """
    
    _instance = None
    
    def __new__(cls):
        """Create or return the singleton instance.
        
        Returns:
            ApiClient: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(ApiClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the API client."""
        if self._initialized:
            return
            
        self._base_url = config.get("api.url", "http://localhost:8080")
        
        # Client and queue
        self._client = None
        self._command_queue = None
        
        # API logging
        self._log_values = config.get("api.log_values", False)
        
        # Create log directory if value logging is enabled
        log_dir = config.get("general.log_dir", "logs")
        self._api_log_dir = os.path.join(log_dir, "api_values")
        
        self._initialized = True
        
        logger.info("API client initialized")
    
    async def start(self):
        """Start the API client.
        
        This initializes the HTTP client and creates a command queue.
        """
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        
        # Create a command queue
        self._command_queue = asyncio.Queue()
        
        # Create API log directory if needed
        if self._log_values:
            os.makedirs(self._api_log_dir, exist_ok=True)
        
        logger.info("API client started")
    
    async def stop(self):
        """Stop the API client.
        
        This method closes the HTTP client.
        """
        if self._client:
            await self._client.aclose()
            self._client = None
            
        logger.info("API Client stopped")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests.
        
        Returns:
            Dict[str, str]: Headers for API requests.
        """
        # Use auth manager to get auth headers
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # Add authorization if authenticated
        if auth_manager.is_authenticated():
            client_id = auth_manager.get_client_id()
            if client_id:
                # Pass client ID in header
                headers["X-Client-ID"] = client_id
            
        return headers
    
    def _get_api_value_logger(self, data_point: Dict[str, Any]) -> logging.Logger:
        """Create a logger for an individual API value.
        
        Args:
            data_point: The data point to create a logger for.
            
        Returns:
            logging.Logger: Logger for the data point.
        """
        # Create a unique identifier for this data point
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Extract meaningful identifiers from the data
        # First try to get integration name
        integration = data_point.get("integration", "")
        if not integration:
            # Look for source as backup
            integration = data_point.get("source", "unknown")
        
        # Try to get endpoint or sensor name
        endpoint = data_point.get("endpoint_name", "")
        if not endpoint:
            # Try sensor name as backup
            endpoint = data_point.get("sensor", "")
            # If still not found, try action or target
            if not endpoint:
                endpoint = data_point.get("action", "") or data_point.get("target", "unknown")
        
        log_id = f"{timestamp}_{integration}_{endpoint}"
        
        # Create a logger for this data point
        value_logger = logging.getLogger(f"api.value.{log_id}")
        
        # Remove existing handlers to prevent duplicates
        for handler in value_logger.handlers[:]:
            value_logger.removeHandler(handler)
        
        # Set level from config
        log_level_name = config.get("general.log_level", "INFO")
        log_level = getattr(logging, log_level_name.upper(), logging.INFO)
        value_logger.setLevel(log_level)
        
        # Create log file name
        log_file = os.path.join(self._api_log_dir, f"{log_id}.log")
        
        # Create file handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_format)
        value_logger.addHandler(file_handler)
        
        return value_logger
    
    async def send_data(self, data_points: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """Send data points to the API.
        
        Args:
            data_points: List of data points to send.
            
        Returns:
            Tuple[bool, str]: (success, message) tuple.
        """
        if not data_points:
            return True, "No data points to send"
            
        if not self._client:
            return False, "API client not started"
        
        # Ensure we're authenticated
        if not auth_manager.is_authenticated():
            return False, "Not authenticated with API"
            
        # Check if the client is ready to send data (space created)
        if not auth_manager.is_ready_for_data():
            # Check the current status
            connected, status = await auth_manager.check_connection_status()
            if connected and status == "connected":
                logger.info("Client is connected but space not created yet, queuing data for later transmission")
                return False, "Client connected but space not created yet, data queued for later transmission"
            elif not connected:
                return False, "Not connected to API"
            
        client_id = auth_manager.get_client_id()
        if not client_id:
            return False, "No client ID available"
            
        url = f"{self._base_url}/client/{client_id}"
        
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
                stop=stop_after_attempt(config.get("api.retry_max_attempts", 5)),
                wait=wait_exponential(
                    min=config.get("api.retry_min_backoff", 1),
                    max=config.get("api.retry_max_backoff", 60),
                ),
            ):
                with attempt:
                    logger.debug(f"Sending {len(data_points)} data points to API")
                    response = await self._client.post(url, json={"data": data_points}, headers=self._get_headers())
                    response.raise_for_status()
            
            # Log each data point individually to its own file
            if self._log_values:
                for data_point in data_points:
                    value_logger = self._get_api_value_logger(data_point)
                    value_logger.info(f"Data sent to API: {json.dumps(data_point)}")
                    
                    # Log additional metadata
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Success")
                
            logger.info(f"Successfully sent {len(data_points)} data points to API")
            return True, "Data sent successfully"
            
        except httpx.HTTPStatusError as e:
            # Log error for each data point
            if self._log_values:
                for data_point in data_points:
                    value_logger = self._get_api_value_logger(data_point)
                    value_logger.error(f"HTTP error sending data: {e.response.status_code} - {e.response.text}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                
            logger.error(f"HTTP error sending data: {e.response.status_code} - {e.response.text}")
            return False, f"HTTP error: {e.response.status_code}"
            
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            # Log error for each data point
            if self._log_values:
                for data_point in data_points:
                    value_logger = self._get_api_value_logger(data_point)
                    value_logger.error(f"Error sending data: {str(e)}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                
            logger.error(f"Error sending data: {str(e)}")
            return False, f"Request error: {str(e)}"
            
        except Exception as e:
            # Log error for each data point
            if self._log_values:
                for data_point in data_points:
                    value_logger = self._get_api_value_logger(data_point)
                    value_logger.error(f"Unexpected error sending data: {str(e)}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                
            logger.exception(f"Unexpected error sending data: {str(e)}")
            return False, f"Unexpected error: {str(e)}"
    
    async def poll_commands(self) -> Optional[List[Dict[str, Any]]]:
        """Poll for commands from the API.
        
        Returns:
            Optional[List[Dict[str, Any]]]: List of commands, or None on error.
        """
        if not self._client:
            logger.error("API client not started")
            return None
            
        # Ensure we're authenticated
        if not auth_manager.is_authenticated():
            logger.warning("Not authenticated, cannot poll commands")
            return None
            
        client_id = auth_manager.get_client_id()
        if not client_id:
            logger.warning("No client ID available, cannot poll commands")
            return None
            
        url = f"{self._base_url}/client/{client_id}"
        
        try:
            response = await self._client.get(url, headers=self._get_headers())
            
            # Handle 204 status code (connected but no space)
            if response.status_code == 204:
                logger.debug("Client is connected but no space created yet, no commands available")
                return []
            
            # Ensure the response is valid
            response.raise_for_status()
            
            data = response.json()
            commands = data.get("commands", [])
            
            if commands:
                logger.info(f"Received {len(commands)} commands from API")
            
            return commands
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error polling commands: {e.response.status_code} - {e.response.text}")
            
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            logger.error(f"Error polling commands: {str(e)}")
            
        except Exception as e:
            logger.exception(f"Unexpected error polling commands: {str(e)}")
            
        return None
    
    async def start_command_polling(self):
        """Start polling for commands in a background task."""
        asyncio.create_task(self._command_polling_task())
    
    async def _command_polling_task(self):
        """Background task for polling commands."""
        interval = config.get("api.poll_interval", 30)  # Default: 30 seconds
        
        while True:
            try:
                commands = await self.poll_commands()
                if commands:
                    for command in commands:
                        await self._command_queue.put(command)
                        
                await asyncio.sleep(interval)
                
            except asyncio.CancelledError:
                logger.info("Command polling task cancelled")
                break
                
            except Exception as e:
                logger.error(f"Error in command polling task: {str(e)}")
                await asyncio.sleep(interval)
    
    async def get_command(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Get a command from the command queue.
        
        Args:
            timeout: Timeout in seconds, or None to wait indefinitely.
            
        Returns:
            Optional[Dict[str, Any]]: Command, or None if timeout occurs.
        """
        try:
            if timeout is None:
                command = await self._command_queue.get()
            else:
                command = await asyncio.wait_for(self._command_queue.get(), timeout)
                
            return command
            
        except asyncio.TimeoutError:
            return None
    
    async def send_command_result(self, command_id: str, success: bool, message: str) -> bool:
        """Send the result of executing a command back to the API.
        
        Args:
            command_id: ID of the command.
            success: Whether the command was executed successfully.
            message: Message describing the result.
            
        Returns:
            bool: True if the result was sent successfully, False otherwise.
        """
        if not self._client:
            logger.error("API client not started")
            return False
            
        # Ensure we're authenticated
        if not auth_manager.is_authenticated():
            return False
            
        client_id = auth_manager.get_client_id()
        if not client_id:
            return False
            
        url = f"{self._base_url}/client/{client_id}/commands/{command_id}/result"
        
        try:
            data = {
                "success": success,
                "message": message,
                "timestamp": int(time.time() * 1000),  # milliseconds since epoch
            }
            
            response = await self._client.post(url, json=data, headers=self._get_headers())
            response.raise_for_status()
            
            logger.info(f"Command result sent successfully: {command_id}, success={success}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending command result: {e}")
            return False

    def get_init_state(self):
        """Get the initialization state of the API client.
        
        Returns:
            dict: A dictionary with initialization state information.
        """
        return {
            "initialized": self._client is not None,
            "has_command_queue": hasattr(self, "_command_queue") and self._command_queue is not None
        }


# Create a global instance for easy imports
api_client = ApiClient() 
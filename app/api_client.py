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
from typing import Any, Dict, List, Optional, Tuple, Union, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.auth import auth_manager
from app.config import config
# Import api_types for the new data format
from app.api_types import (
    ActionType, LogType, ProblemType, ProblemStatus,
    create_data_log, create_problem, create_action_response, parse_api_response
)


logger = logging.getLogger(__name__)


class ApiClient:
    """Client for interacting with the GrowAssistant API.
    
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
        
        # Initialize data storage for new API format
        self._data_logs = []
        self._problems = []
        self._actions = []
        
        # Action handling
        self._pending_actions = {}  # Actions waiting for resolution
        self._action_handlers = {}  # Callbacks for handling actions

        # Settings handling
        self._settings_callback = None  # Callback for when settings are received

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
    
    # New methods for API data format
    def add_data_log(self, log_type: Union[LogType, str], value: Union[str, float, int], log_date: Optional[datetime] = None, pump_num: Optional[int] = None):
        """Add a data log entry.

        Args:
            log_type: Type of log (can be string or LogType enum)
            value: Value to log
            log_date: Timestamp (defaults to now)
            pump_num: Optional pump number (only for pump-related logs)
        """
        data_log = create_data_log(log_type, value, log_date)
        if pump_num is not None:
            data_log["pumpNum"] = pump_num
        self._data_logs.append(data_log)
        
    def add_problem(self, problem_type: Union[ProblemType, str], status: Union[ProblemStatus, str], 
                    description: str, priority: int = 0, user_can_resolve: bool = True, 
                    resolved: bool = False, problem_id: Optional[str] = None):
        """Add a problem report.
        
        Args:
            problem_type: Type of problem
            status: Problem status category
            description: Description of the problem
            priority: Priority level (0-100)
            user_can_resolve: Whether user can resolve
            resolved: Whether already resolved
            problem_id: Optional ID (generated if not provided)
        """
        self._problems.append(create_problem(
            problem_type, status, description, priority,
            user_can_resolve, resolved, problem_id
        ))
        
    def acknowledge_action(self, action_id: str, received: bool = True, resolved: bool = False):
        """Acknowledge an action from the API.
        
        Args:
            action_id: ID of the action
            received: Whether action was received
            resolved: Whether action was completed
        """
        self._actions.append(create_action_response(action_id, received, resolved))
        
    def register_action_handler(self, action_type: Union[ActionType, str], handler: Callable):
        """Register a handler for a specific action type.

        Args:
            action_type: Type of action to handle
            handler: Callback function(action_data) -> bool
        """
        # Convert enum to string if needed
        if isinstance(action_type, ActionType):
            action_type = action_type.value

        self._action_handlers[action_type] = handler

    def register_settings_callback(self, callback: Callable):
        """Register a callback for when settings are received from the API.

        Args:
            callback: Async callback function(settings: Dict) -> None
        """
        self._settings_callback = callback

    def _detect_problems_from_data(self, data_points: List[Dict[str, Any]]):
        """Detect problems from data points.

        This method analyzes data points for potential issues like:
        - Out of range values
        - Sensor failures
        - Connection issues

        Args:
            data_points: List of data points to analyze
        """
        # Define acceptable ranges for different sensor types
        # Maps data type -> problem type for that sensor
        type_mapping = {
            "TEMPERATURE": ProblemType.TEMPERATURE,
            "HUMIDITY": ProblemType.HUMIDITY,
            "PH": ProblemType.PH,
            "PH_VALUE": ProblemType.PH,
            "TANK_ML": ProblemType.TANK,
            "SUPPLEMENT_ML": ProblemType.SUPPLEMENT,
            "LIGHT": ProblemType.LIGHT,
            "FAN": ProblemType.FAN,
        }

        ranges = {
            "TEMPERATURE": {"min": -10, "max": 50},
            "HUMIDITY": {"min": 0, "max": 100},
            "PH": {"min": 0, "max": 14},
            "PH_VALUE": {"min": 0, "max": 14},
            "TANK_ML": {"min": 0, "max": None},
        }

        for point in data_points:
            data_type = point.get("type", "").upper()
            value = point.get("value")
            integration = point.get("integration", "Unknown")

            # Skip if no value or type
            if value is None or not data_type:
                continue

            # Get the problem type for this data type
            problem_type = type_mapping.get(data_type, ProblemType.CLIENT)

            # Check if value indicates a sensor failure (null, error, etc.)
            if isinstance(value, str) and value.lower() in ["error", "failed", "null", "none", "unavailable"]:
                self.add_problem(
                    problem_type=problem_type,
                    status=ProblemStatus.CONNECTION,  # Sensor failure is a connection issue
                    description=f"Sensor failure detected for {data_type} from {integration}",
                    priority=70,
                    user_can_resolve=False,
                    resolved=False
                )
                continue

            # Try to convert value to float for range checking
            try:
                numeric_value = float(value)

                # Check if value is out of acceptable range
                if data_type in ranges:
                    range_config = ranges[data_type]
                    min_val = range_config.get("min")
                    max_val = range_config.get("max")

                    if (min_val is not None and numeric_value < min_val) or \
                       (max_val is not None and numeric_value > max_val):
                        self.add_problem(
                            problem_type=problem_type,
                            status=ProblemStatus.RANGE,
                            description=f"{data_type} value {numeric_value} is out of range from {integration}",
                            priority=50,
                            user_can_resolve=True,
                            resolved=False
                        )

            except (ValueError, TypeError):
                # Value is not numeric, which might be okay for some types
                pass
    
    async def send_data(self, data_points: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, str]:
        """Send data to the API.
        
        Args:
            data_points: Optional legacy data points (for backward compatibility)
            
        Returns:
            Tuple[bool, str]: (success, message) tuple.
        """
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
        
        # Process legacy data points if provided (backward compatibility)
        if data_points:
            # Detect problems from the data points first
            self._detect_problems_from_data(data_points)

            for point in data_points:
                # Convert legacy data points to new format
                log_type = point.get("type", "SYSTEM")
                value = point.get("value", "")
                timestamp = point.get("timestamp")
                if timestamp:
                    log_date = datetime.fromtimestamp(timestamp / 1000)  # Convert from milliseconds
                else:
                    log_date = None

                # Extract pumpNum if present (only for pump-related data)
                pump_num = point.get("pumpNum") or point.get("pump_num")

                self.add_data_log(log_type, value, log_date, pump_num)

        # Prepare payload in the new format
        payload = {
            "dataLogs": self._data_logs,
            "problems": self._problems,
            "actions": self._actions
        }
        
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
                    logger.debug(f"Sending data to API: {len(self._data_logs)} logs, "
                                f"{len(self._problems)} problems, {len(self._actions)} actions")
                    response = await self._client.post(url, json=payload, headers=self._get_headers())
                    response.raise_for_status()
                    
                    # Process response to extract actions
                    response_data = response.json()
                    await self._process_response(response_data)
            
            # Log each data point individually to its own file
            if self._log_values:
                # Log data logs
                for data_log in self._data_logs:
                    value_logger = self._get_api_value_logger({
                        "type": data_log.get("logType"),
                        "value": data_log.get("value"),
                        "timestamp": data_log.get("logDate")
                    })
                    value_logger.info(f"Data log sent to API: {json.dumps(data_log)}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Success")
                
                # Log problems
                for problem in self._problems:
                    value_logger = self._get_api_value_logger({
                        "type": problem.get("type"),
                        "status": problem.get("status"),
                        "description": problem.get("description")
                    })
                    value_logger.info(f"Problem sent to API: {json.dumps(problem)}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Success")
                
                # Log actions
                for action in self._actions:
                    value_logger = self._get_api_value_logger({
                        "action": action.get("id"),
                        "received": action.get("received"),
                        "resolved": action.get("resolved")
                    })
                    value_logger.info(f"Action response sent to API: {json.dumps(action)}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Success")
                
            # Clear sent data
            data_logs_count = len(self._data_logs)
            problems_count = len(self._problems)
            actions_count = len(self._actions)
            
            self._data_logs = []
            self._problems = []
            self._actions = []
            
            logger.info(f"Successfully sent {data_logs_count} data logs, {problems_count} problems, and {actions_count} actions to API")
            return True, "Data sent successfully"
            
        except httpx.HTTPStatusError as e:
            # Log error for each data type
            if self._log_values:
                # Log data logs errors
                for data_log in self._data_logs:
                    value_logger = self._get_api_value_logger({
                        "type": data_log.get("logType"),
                        "value": data_log.get("value")
                    })
                    value_logger.error(f"HTTP error sending data log: {e.response.status_code} - {e.response.text}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                
                # Similar logging for problems and actions
                for problem in self._problems:
                    value_logger = self._get_api_value_logger({
                        "type": problem.get("type"),
                        "status": problem.get("status"),
                        "description": problem.get("description")
                    })
                    value_logger.error(f"HTTP error sending problem: {e.response.status_code} - {e.response.text}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                for action in self._actions:
                    value_logger = self._get_api_value_logger({
                        "action": action.get("id"),
                        "received": action.get("received"),
                        "resolved": action.get("resolved")
                    })
                    value_logger.error(f"HTTP error sending action: {e.response.status_code} - {e.response.text}")
                    value_logger.info(f"API URL: {url}")
                    value_logger.info(f"Client ID: {client_id}")
                    value_logger.info(f"Status: Failed")
                    
                    
                
            logger.error(f"HTTP error sending data: {e.response.status_code} - {e.response.text}")
            return False, f"HTTP error: {e.response.status_code}"
            
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            # Similar error handling to current implementation
            logger.error(f"Error sending data: {str(e)}")
            return False, f"Request error: {str(e)}"
            
        except Exception as e:
            # Similar error handling to current implementation
            logger.exception(f"Unexpected error sending data: {str(e)}")
            return False, f"Unexpected error: {str(e)}"
    
    async def _process_response(self, response_data: Dict[str, Any]):
        """Process API response data.

        Args:
            response_data: Raw response data from API
        """
        # Parse the response
        parsed = parse_api_response(response_data)

        # Extract and process settings from response
        settings = {
            "rdh_mode": parsed.get("rdh_mode", False),
            "status": parsed.get("status", ""),
            "light": parsed.get("light", {}),
            "climate": parsed.get("climate", {}),
            "tank": parsed.get("tank", {})
        }

        # Call settings callback if registered
        if self._settings_callback:
            try:
                await self._settings_callback(settings)
                logger.debug("Settings callback executed successfully")
            except Exception as e:
                logger.error(f"Error in settings callback: {e}")

        # Process actions
        for action in parsed.get("actions", []):
            action_id = action.get("id")
            action_type = action.get("type")

            if not action_id or not action_type:
                logger.warning(f"Received action with missing id or type: {action}")
                continue

            # Mark as received
            self.acknowledge_action(action_id, received=True, resolved=False)

            # Try to handle the action with registered handler
            if action_type in self._action_handlers:
                try:
                    handler = self._action_handlers[action_type]
                    # Call the handler asynchronously
                    success = await handler(action)

                    # If handler was successful, mark as resolved
                    if success:
                        self.acknowledge_action(action_id, received=True, resolved=True)

                    logger.info(f"Handled action {action_id} of type {action_type}, success: {success}")

                except Exception as e:
                    logger.error(f"Error handling action {action_id} of type {action_type}: {e}")
            else:
                logger.warning(f"No handler registered for action type: {action_type}")
                
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
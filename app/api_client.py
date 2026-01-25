"""API Client for communicating with the Spring API."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Optional, Union

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.api_types import (
    ActionType,
    LogType,
    ProblemStatus,
    ProblemType,
    create_action_response,
    create_data_log,
    create_problem,
    parse_api_response,
)
from app.auth import auth_manager
from app.config import config
from app.constants import (
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_BACKOFF,
    DEFAULT_RETRY_MIN_BACKOFF,
    ProblemPriority,
    SensorRanges,
)
from app.utils.http_utils import build_auth_headers
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class ApiClient(metaclass=SingletonMeta):
    """Client for interacting with the GrowAssistant API.

    Handles authentication, data transmission, and command reception.
    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the API client."""
        self._base_url = config.get("api.url", "http://localhost:8080")
        self._client: Optional[httpx.AsyncClient] = None
        self._command_queue: Optional[asyncio.Queue] = None
        self._log_values = config.get("api.log_values", False)
        self._api_log_dir = os.path.join(config.get("general.log_dir", "logs"), "api_values")

        # Data storage for API format
        self._data_logs: list[dict] = []
        self._problems: list[dict] = []
        self._actions: list[dict] = []

        # Action and settings handling
        self._pending_actions: dict[str, dict] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._settings_callback: Optional[Callable] = None

        logger.info("API client initialized")

    async def start(self):
        """Start the API client and initialize HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=config.get("api.timeout", DEFAULT_HTTP_TIMEOUT),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=config.get("api.verify_ssl", True),
        )
        self._command_queue = asyncio.Queue()

        if self._log_values:
            os.makedirs(self._api_log_dir, exist_ok=True)

        logger.info("API client started")

    async def stop(self):
        """Stop the API client and close HTTP connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("API Client stopped")

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        client_id = auth_manager.get_client_id() if auth_manager.is_authenticated() else None
        return build_auth_headers(client_id=client_id)

    def _get_api_value_logger(self, data_point: dict[str, Any]) -> logging.Logger:
        """Create a logger for an individual API value."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        integration = data_point.get("integration") or data_point.get("source", "unknown")
        endpoint = (
            data_point.get("endpoint_name")
            or data_point.get("sensor")
            or data_point.get("action")
            or data_point.get("target", "unknown")
        )
        log_id = f"{timestamp}_{integration}_{endpoint}"

        value_logger = logging.getLogger(f"api.value.{log_id}")
        for handler in value_logger.handlers[:]:
            value_logger.removeHandler(handler)

        log_level = getattr(logging, config.get("general.log_level", "INFO").upper(), logging.INFO)
        value_logger.setLevel(log_level)

        file_handler = logging.FileHandler(os.path.join(self._api_log_dir, f"{log_id}.log"))
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        value_logger.addHandler(file_handler)

        return value_logger

    def add_data_log(
        self,
        log_type: Union[LogType, str],
        value: Union[str, float, int],
        log_date: Optional[datetime] = None,
        pump_num: Optional[int] = None,
    ):
        """Add a data log entry."""
        data_log = create_data_log(log_type, value, log_date)
        if pump_num is not None:
            data_log["pumpNum"] = pump_num
        self._data_logs.append(data_log)

    def add_problem(
        self,
        problem_type: Union[ProblemType, str],
        status: Union[ProblemStatus, str],
        description: str,
        priority: int = 0,
        user_can_resolve: bool = True,
        resolved: bool = False,
        problem_id: Optional[str] = None,
    ):
        """Add a problem report."""
        self._problems.append(
            create_problem(
                problem_type, status, description, priority, user_can_resolve, resolved, problem_id
            )
        )

    def acknowledge_action(self, action_id: str, received: bool = True, resolved: bool = False):
        """Acknowledge an action from the API."""
        self._actions.append(create_action_response(action_id, received, resolved))

    def register_action_handler(self, action_type: Union[ActionType, str], handler: Callable):
        """Register a handler for a specific action type."""
        key = action_type.value if isinstance(action_type, ActionType) else action_type
        self._action_handlers[key] = handler

    def register_settings_callback(self, callback: Callable):
        """Register a callback for when settings are received from the API."""
        self._settings_callback = callback

    def _validate_send_preconditions(self) -> tuple[bool, str, Optional[str]]:
        """Validate preconditions for sending data.

        Returns:
            Tuple of (success, error_message, client_id).
        """
        if not self._client:
            return False, "API client not started", None
        if not auth_manager.is_authenticated():
            return False, "Not authenticated with API", None
        client_id = auth_manager.get_client_id()
        if not client_id:
            return False, "No client ID available", None
        return True, "", client_id

    def _process_legacy_data_points(self, data_points: list[dict[str, Any]]) -> None:
        """Process legacy data points and convert to new format."""
        self._detect_problems_from_data(data_points)

        for point in data_points:
            timestamp = point.get("timestamp")
            log_date = datetime.fromtimestamp(timestamp / 1000) if timestamp else None
            self.add_data_log(
                log_type=point.get("type", "SYSTEM"),
                value=point.get("value", ""),
                log_date=log_date,
                pump_num=point.get("pumpNum") or point.get("pump_num"),
            )

    def _log_transmission_results(
        self, url: str, client_id: str, success: bool, error_msg: Optional[str] = None
    ) -> None:
        """Log transmission results for each data item."""
        if not self._log_values:
            return

        status = "Success" if success else "Failed"

        def log_item(value_logger: logging.Logger, item_type: str, item: dict):
            if success:
                value_logger.info(f"{item_type} sent to API: {json.dumps(item)}")
            else:
                value_logger.error(f"Error sending {item_type.lower()}: {error_msg}")
            value_logger.info(f"API URL: {url}, Client ID: {client_id}, Status: {status}")

        for data_log in self._data_logs:
            value_logger = self._get_api_value_logger(
                {
                    "type": data_log.get("logType"),
                    "value": data_log.get("value"),
                    "timestamp": data_log.get("logDate"),
                }
            )
            log_item(value_logger, "Data log", data_log)

        for problem in self._problems:
            value_logger = self._get_api_value_logger(
                {
                    "type": problem.get("type"),
                    "status": problem.get("status"),
                    "description": problem.get("description"),
                }
            )
            log_item(value_logger, "Problem", problem)

        for action in self._actions:
            value_logger = self._get_api_value_logger(
                {
                    "action": action.get("id"),
                    "received": action.get("received"),
                    "resolved": action.get("resolved"),
                }
            )
            log_item(value_logger, "Action response", action)

    def _clear_sent_data(self) -> tuple[int, int, int]:
        """Clear sent data and return counts of (data_logs, problems, actions)."""
        counts = (len(self._data_logs), len(self._problems), len(self._actions))
        self._data_logs, self._problems, self._actions = [], [], []
        return counts

    def _detect_problems_from_data(self, data_points: list[dict[str, Any]]):
        """Detect problems from data points (out of range values, sensor failures)."""
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
            "TEMPERATURE": (SensorRanges.TEMPERATURE_MIN, SensorRanges.TEMPERATURE_MAX),
            "HUMIDITY": (SensorRanges.HUMIDITY_MIN, SensorRanges.HUMIDITY_MAX),
            "PH": (SensorRanges.PH_MIN, SensorRanges.PH_MAX),
            "PH_VALUE": (SensorRanges.PH_MIN, SensorRanges.PH_MAX),
            "TANK_ML": (SensorRanges.TANK_ML_MIN, SensorRanges.TANK_ML_MAX),
        }

        failure_values = {"error", "failed", "null", "none", "unavailable"}

        for point in data_points:
            data_type = point.get("type", "").upper()
            value = point.get("value")
            integration = point.get("integration", "Unknown")

            if value is None or not data_type:
                continue

            problem_type = type_mapping.get(data_type, ProblemType.CLIENT)

            # Check for sensor failure
            if isinstance(value, str) and value.lower() in failure_values:
                self.add_problem(
                    problem_type=problem_type,
                    status=ProblemStatus.CONNECTION,
                    description=f"Sensor failure detected for {data_type} from {integration}",
                    priority=ProblemPriority.HIGH,
                    user_can_resolve=False,
                )
                continue

            # Check range
            try:
                numeric_value = float(value)
                if data_type in ranges:
                    min_val, max_val = ranges[data_type]
                    out_of_range = (min_val is not None and numeric_value < min_val) or (
                        max_val is not None and numeric_value > max_val
                    )
                    if out_of_range:
                        self.add_problem(
                            problem_type=problem_type,
                            status=ProblemStatus.RANGE,
                            description=f"{data_type} value {numeric_value} out of range from {integration}",
                            priority=ProblemPriority.MEDIUM,
                        )
            except (ValueError, TypeError):
                pass

    async def send_data(
        self, data_points: Optional[list[dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        """Send data to the API. Returns (success, message) tuple."""
        valid, error_msg, client_id = self._validate_send_preconditions()
        if not valid:
            return False, error_msg

        if not auth_manager.is_ready_for_data():
            connected, status = await auth_manager.check_connection_status()
            if connected and status == "connected":
                logger.info("Client connected but space not created yet, queuing data")
                return False, "Client connected but space not created yet"
            if not connected:
                return False, "Not connected to API"

        url = f"{self._base_url}/client/{client_id}"

        if data_points:
            self._process_legacy_data_points(data_points)

        payload = {
            "dataLogs": self._data_logs,
            "problems": self._problems,
            "actions": self._actions,
        }

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
                stop=stop_after_attempt(
                    config.get("api.retry_max_attempts", DEFAULT_RETRY_MAX_ATTEMPTS)
                ),
                wait=wait_exponential(
                    min=config.get("api.retry_min_backoff", DEFAULT_RETRY_MIN_BACKOFF),
                    max=config.get("api.retry_max_backoff", DEFAULT_RETRY_MAX_BACKOFF),
                ),
            ):
                with attempt:
                    logger.debug(
                        f"Sending data: {len(self._data_logs)} logs, "
                        f"{len(self._problems)} problems, {len(self._actions)} actions"
                    )
                    response = await self._client.post(
                        url, json=payload, headers=self._get_headers()
                    )
                    response.raise_for_status()
                    await self._process_response(response.json())

            self._log_transmission_results(url, client_id, success=True)
            logs, problems, actions = self._clear_sent_data()
            logger.info(f"Sent {logs} data logs, {problems} problems, {actions} actions")
            return True, "Data sent successfully"

        except httpx.HTTPStatusError as e:
            error_msg = f"{e.response.status_code} - {e.response.text}"
            self._log_transmission_results(url, client_id, success=False, error_msg=error_msg)
            logger.error(f"HTTP error sending data: {error_msg}")
            return False, f"HTTP error: {e.response.status_code}"

        except (httpx.RequestError, asyncio.TimeoutError) as e:
            logger.error(f"Error sending data: {e}")
            return False, f"Request error: {e}"

        except Exception as e:
            logger.exception(f"Unexpected error sending data: {e}")
            return False, f"Unexpected error: {e}"

    async def _process_response(self, response_data: dict[str, Any]):
        """Process API response data including settings and actions."""
        parsed = parse_api_response(response_data)

        settings = {
            "rdh_mode": parsed.get("rdh_mode", False),
            "status": parsed.get("status", ""),
            "light": parsed.get("light", {}),
            "climate": parsed.get("climate", {}),
            "tank": parsed.get("tank", {}),
        }

        if self._settings_callback:
            try:
                await self._settings_callback(settings)
                logger.debug("Settings callback executed successfully")
            except Exception as e:
                logger.error(f"Error in settings callback: {e}")

        for action in parsed.get("actions", []):
            action_id, action_type = action.get("id"), action.get("type")
            if not action_id or not action_type:
                logger.warning(f"Received action with missing id or type: {action}")
                continue

            self.acknowledge_action(action_id, received=True, resolved=False)

            if action_type not in self._action_handlers:
                logger.warning(f"No handler registered for action type: {action_type}")
                continue

            try:
                success = await self._action_handlers[action_type](action)
                if success:
                    self.acknowledge_action(action_id, received=True, resolved=True)
                logger.info(f"Handled action {action_id} ({action_type}): success={success}")
            except Exception as e:
                logger.error(f"Error handling action {action_id} ({action_type}): {e}")

    async def poll_commands(self) -> Optional[list[dict[str, Any]]]:
        """Poll for commands from the API. Returns list of commands or None on error."""
        if not self._client:
            logger.error("API client not started")
            return None
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

            if response.status_code == 204:
                logger.debug("Client connected but no space created yet")
                return []

            response.raise_for_status()
            commands = response.json().get("commands", [])
            if commands:
                logger.info(f"Received {len(commands)} commands from API")
            return commands

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error polling commands: {e.response.status_code} - {e.response.text}"
            )
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            logger.error(f"Error polling commands: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error polling commands: {e}")

        return None

    async def start_command_polling(self):
        """Start polling for commands in a background task."""
        asyncio.create_task(self._command_polling_task())

    async def _command_polling_task(self):
        """Background task for polling commands."""
        interval = config.get("api.poll_interval", 30)

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
                logger.error(f"Error in command polling task: {e}")
                await asyncio.sleep(interval)

    async def get_command(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get a command from the queue. Returns None on timeout."""
        try:
            if timeout is None:
                return await self._command_queue.get()
            return await asyncio.wait_for(self._command_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def send_command_result(self, command_id: str, success: bool, message: str) -> bool:
        """Send the result of executing a command back to the API."""
        if not self._client or not auth_manager.is_authenticated():
            logger.error("API client not started or not authenticated")
            return False

        client_id = auth_manager.get_client_id()
        if not client_id:
            return False

        url = f"{self._base_url}/client/{client_id}/commands/{command_id}/result"

        try:
            data = {
                "success": success,
                "message": message,
                "timestamp": int(time.time() * 1000),
            }
            response = await self._client.post(url, json=data, headers=self._get_headers())
            response.raise_for_status()
            logger.info(f"Command result sent: {command_id}, success={success}")
            return True
        except Exception as e:
            logger.error(f"Error sending command result: {e}")
            return False

    def get_init_state(self) -> dict[str, bool]:
        """Get the initialization state of the API client."""
        return {
            "initialized": self._client is not None,
            "has_command_queue": self._command_queue is not None,
        }


# Create a global instance for easy imports
api_client = ApiClient()

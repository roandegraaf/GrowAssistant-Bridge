"""API Client for communicating with the Spring API via SSE and REST."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Optional, Union

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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
from app.config_store import config_store
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

# SSE reconnect constants
SSE_RECONNECT_MIN = 1
SSE_RECONNECT_MAX = 60
SSE_STREAM_TIMEOUT = 90.0  # Longer than heartbeat interval (15s) to detect stale connections


class ApiClient(metaclass=SingletonMeta):
    """Client for interacting with the GrowAssistant API.

    Handles data transmission via REST and receives config/actions via SSE.
    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the API client."""
        self._base_url = config.get("api.url", "http://localhost:8080").rstrip("/")
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

        # SSE state
        self._sse_task: Optional[asyncio.Task] = None
        self._sse_running = False

        # Manifest push state
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._manifest_lock: Optional[asyncio.Lock] = None

        logger.info("API client initialized")

    async def start(self):
        """Start the API client and initialize HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=config.get("api.timeout", DEFAULT_HTTP_TIMEOUT),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=config.get("api.verify_ssl", True),
        )
        self._command_queue = asyncio.Queue()

        # Capture the running loop so the registry change callback (which
        # may fire from any thread / sync context) can schedule the async
        # manifest push correctly.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # Wire registry change callbacks → manifest push.
        from app.registry import registry as _registry

        _registry.add_change_callback(self._on_registry_change)

        if self._log_values:
            os.makedirs(self._api_log_dir, exist_ok=True)

        logger.info("API client started")

    async def stop(self):
        """Stop the API client, SSE listener, and close HTTP connection."""
        # Deregister the registry change callback so a shutting-down client
        # never schedules another manifest push.
        try:
            from app.registry import registry as _registry

            _registry.remove_change_callback(self._on_registry_change)
        except Exception:
            logger.debug("Failed to deregister registry change callback", exc_info=True)

        self._sse_running = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            self._sse_task = None

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

    # ─── Data log / problem / action building ───────────────────────

    def add_data_log(
        self,
        log_type: Union[LogType, str],
        value: Union[str, float, int],
        log_date: Optional[datetime] = None,
        device_id: Optional[str] = None,
    ):
        """Add a data log entry."""
        data_log = create_data_log(log_type, value, log_date, device_id)
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

    # ─── Validation helpers ─────────────────────────────────────────

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

    # ─── Data processing helpers ────────────────────────────────────

    @staticmethod
    def _derive_entity_id(point: dict[str, Any]) -> Optional[str]:
        """Best-effort derive a stable `<domain>.<name>` entity_id from a legacy point.

        Each integration's `receive_data()` yields a different key for the device
        name (GPIO → `pin_name`, MQTT → `topic`, HTTP → `endpoint_name`, Serial
        has no consistent key). `main._data_collection_task` tags every point with
        `integration` (the registered integration name). We strip a trailing
        `Integration` suffix to get the domain, then probe a series of keys in
        order of specificity.

        Returns None when no name can be found — `add_data_log` will then omit the
        device_id and the API will skip the liveness touch for that point.
        """
        # If the integration already produced a fully-qualified entity_id, trust it.
        explicit = point.get("entity_id")
        if isinstance(explicit, str) and "." in explicit:
            return explicit

        integration = point.get("integration")
        if not integration:
            return None
        domain = integration.lower()
        if domain.endswith("integration"):
            domain = domain[: -len("integration")]

        name = (
            point.get("device_id")
            or point.get("device")
            or point.get("entity_id")
            or point.get("sensor")
            or point.get("endpoint_name")  # HTTP integration
            or point.get("topic")  # MQTT integration
            or point.get("pin_name")  # GPIO integration
            or point.get("name")
            or point.get("target")
        )
        return f"{domain}.{name}" if name else None

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
                device_id=self._derive_entity_id(point),
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

    # ─── REST: Send sensor data ─────────────────────────────────────

    async def send_data(
        self, data_points: Optional[list[dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        """Send sensor data to the API. Returns (success, message) tuple.

        Posts to POST /bridge/{id}/data with payload {"dataLogs": [...]}.
        The endpoint returns 200 OK with no body (config comes via SSE).
        """
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

        url = f"{self._base_url}/bridge/{client_id}/data"

        if data_points:
            self._process_legacy_data_points(data_points)

        payload = {
            "dataLogs": self._data_logs,
        }

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(
                    (httpx.HTTPError, httpx.ConnectError, asyncio.TimeoutError)
                ),
                stop=stop_after_attempt(
                    config.get("api.retry_max_attempts", DEFAULT_RETRY_MAX_ATTEMPTS)
                ),
                wait=wait_exponential(
                    min=config.get("api.retry_min_backoff", DEFAULT_RETRY_MIN_BACKOFF),
                    max=config.get("api.retry_max_backoff", DEFAULT_RETRY_MAX_BACKOFF),
                ),
            ):
                with attempt:
                    logger.debug(f"Sending data: {len(self._data_logs)} logs")
                    response = await self._client.post(
                        url, json=payload, headers=self._get_headers()
                    )
                    response.raise_for_status()

            self._log_transmission_results(url, client_id, success=True)
            logs, problems, actions = self._clear_sent_data()
            logger.info(f"Sent {logs} data logs")
            return True, "Data sent successfully"

        except RetryError as e:
            self._clear_sent_data()
            original_exception = e.last_attempt.exception()
            if isinstance(original_exception, (httpx.ConnectError, httpx.ConnectTimeout)):
                logger.warning(
                    f"API connection failed after retries (API offline): {original_exception}"
                )
                return False, "API offline - connection failed"
            elif isinstance(original_exception, asyncio.TimeoutError):
                logger.warning(f"API request timed out after retries: {original_exception}")
                return False, "API offline - request timed out"
            else:
                logger.warning(f"API request failed after retries: {original_exception}")
                return False, f"API unavailable: {original_exception}"

        except httpx.HTTPStatusError as e:
            self._clear_sent_data()
            error_msg = f"{e.response.status_code} - {e.response.text}"
            self._log_transmission_results(url, client_id, success=False, error_msg=error_msg)
            logger.error(f"HTTP error sending data: {error_msg}")
            return False, f"HTTP error: {e.response.status_code}"

        except httpx.ConnectError as e:
            self._clear_sent_data()
            logger.warning(f"API connection failed (API offline): {e}")
            return False, "API offline - connection failed"

        except (httpx.RequestError, asyncio.TimeoutError) as e:
            self._clear_sent_data()
            logger.warning(f"Request error sending data: {e}")
            return False, f"Request error: {e}"

        except Exception as e:
            self._clear_sent_data()
            logger.exception(f"Unexpected error sending data: {e}")
            return False, f"Unexpected error: {e}"

    # ─── REST: Send device manifest ─────────────────────────────────

    def _on_registry_change(self) -> None:
        """Sync callback invoked by the registry when devices change.

        Schedules an async manifest push on the event loop. No-ops cleanly
        when the loop isn't running yet (registry pre-init) or when not
        authenticated — ``send_manifest`` itself re-validates preconditions.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            # No live loop: bridge is starting up or already torn down.
            return
        if not auth_manager.is_authenticated():
            # Not yet authenticated: startup will push once auth completes.
            return
        try:
            if loop.is_running() and asyncio.get_event_loop() is loop:
                asyncio.create_task(self.send_manifest())
            else:
                # Called from a non-loop thread (e.g. signal handler, web UI).
                asyncio.run_coroutine_threadsafe(self.send_manifest(), loop)
        except RuntimeError:
            # No running loop in this thread; fall back to threadsafe schedule.
            try:
                asyncio.run_coroutine_threadsafe(self.send_manifest(), loop)
            except Exception:
                logger.debug(
                    "Could not schedule manifest push from registry callback", exc_info=True
                )

    async def send_manifest(self) -> tuple[bool, str]:
        """Push the current device registry as a manifest to the API.

        Persists a monotonic ``manifestVersion`` and the manifest content
        hash via ``config_store``. Idempotent under concurrent calls (an
        async lock serializes pushes).

        Returns:
            ``(success, message)``.
        """
        valid, error_msg, client_id = self._validate_send_preconditions()
        if not valid:
            return False, error_msg

        # Lazy-init the lock — start() runs on the loop so it's safe here.
        if self._manifest_lock is None:
            self._manifest_lock = asyncio.Lock()

        # Local imports to avoid module import-cycle pain.
        from app.registry import registry

        async with self._manifest_lock:
            # Bump version monotonically. Persist optimistically; if the API
            # rejects we leave the version alone — the API echoes the
            # accepted version which we then write back.
            current_version = config_store.get_manifest_version()
            next_version = current_version + 1

            payload = registry.serialize_manifest(next_version)
            manifest_hash = registry.compute_manifest_hash()
            url = f"{self._base_url}/bridge/{client_id}/manifest"

            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(
                        (httpx.HTTPError, httpx.ConnectError, asyncio.TimeoutError)
                    ),
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
                            f"Pushing manifest v{next_version} "
                            f"with {len(payload['devices'])} devices"
                        )
                        response = await self._client.post(
                            url, json=payload, headers=self._get_headers()
                        )
                        response.raise_for_status()

                # Parse and persist accepted version + content hash.
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    body = {}
                accepted = body.get("acceptedVersion", next_version)
                try:
                    accepted_int = int(accepted)
                except (TypeError, ValueError):
                    accepted_int = next_version
                config_store.set_manifest_version(accepted_int)
                config_store.set_manifest_hash(manifest_hash)
                logger.info(
                    f"Manifest pushed: v{accepted_int}, "
                    f"{len(payload['devices'])} devices, hash={manifest_hash[:12]}…"
                )
                return True, f"Manifest v{accepted_int} accepted"

            except RetryError as e:
                original = e.last_attempt.exception()
                logger.warning(f"Manifest push failed after retries: {original}")
                return False, f"Manifest push failed: {original}"
            except httpx.HTTPStatusError as e:
                err = f"{e.response.status_code} - {e.response.text}"
                logger.error(f"HTTP error pushing manifest: {err}")
                return False, f"HTTP {e.response.status_code}"
            except httpx.ConnectError as e:
                logger.warning(f"Manifest push: API offline ({e})")
                return False, "API offline"
            except (httpx.RequestError, asyncio.TimeoutError) as e:
                logger.warning(f"Request error pushing manifest: {e}")
                return False, f"Request error: {e}"
            except Exception as e:
                logger.exception(f"Unexpected error pushing manifest: {e}")
                return False, f"Unexpected error: {e}"

    # ─── REST: Send action result ───────────────────────────────────

    async def send_command_result(self, command_id: str, success: bool, message: str) -> bool:
        """Send the result of executing a command back to the API."""
        if not self._client or not auth_manager.is_authenticated():
            logger.error("API client not started or not authenticated")
            return False

        client_id = auth_manager.get_client_id()
        if not client_id:
            return False

        url = f"{self._base_url}/bridge/{client_id}/actions/{command_id}/result"

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

    # ─── REST: Fetch full config (fallback) ─────────────────────────

    async def fetch_full_config(self) -> Optional[dict]:
        """Fetch the full config from GET /bridge/{id} as a fallback.

        Returns the parsed config dict, or None on failure.
        """
        if not self._client or not auth_manager.is_authenticated():
            return None

        client_id = auth_manager.get_client_id()
        if not client_id:
            return None

        url = f"{self._base_url}/bridge/{client_id}"

        try:
            response = await self._client.get(url, headers=self._get_headers())
            if response.status_code == 200:
                data = response.json()
                version = data.get("configVersion", 0)
                config_store.save_full_config(data, version)
                logger.info(f"Fetched full config version={version} from API")
                return data
            elif response.status_code == 204:
                logger.info("Bridge connected but no space created yet")
                return None
            else:
                logger.warning(f"Unexpected status fetching config: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching full config: {e}")
            return None

    # ─── Command queue (fed by SSE, consumed by main.py) ────────────

    async def get_command(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get a command from the queue. Returns None on timeout."""
        try:
            if timeout is None:
                return await self._command_queue.get()
            return await asyncio.wait_for(self._command_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    # ─── SSE Listener ───────────────────────────────────────────────

    async def start_sse_listener(self):
        """Start the SSE listener as a background task."""
        self._sse_running = True
        self._sse_task = asyncio.create_task(self._sse_listener_loop())
        logger.info("SSE listener started")

    async def _sse_listener_loop(self):
        """Background loop that connects to SSE and reconnects on failure."""
        backoff = SSE_RECONNECT_MIN

        while self._sse_running:
            try:
                await self._sse_connect()
                # If _sse_connect returns normally (stream ended), reset backoff
                backoff = SSE_RECONNECT_MIN
            except asyncio.CancelledError:
                logger.info("SSE listener cancelled")
                return
            except Exception as e:
                logger.error(f"SSE connection error: {e}")

            if not self._sse_running:
                return

            logger.info(f"SSE reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, SSE_RECONNECT_MAX)

    async def _sse_connect(self):
        """Connect to the SSE stream and process events."""
        client_id = auth_manager.get_client_id()
        if not client_id:
            logger.warning("No client ID for SSE connection")
            await asyncio.sleep(5)
            return

        local_version = config_store.get_config_version()
        url = f"{self._base_url}/bridge/{client_id}/stream?configVersion={local_version}"
        headers = self._get_headers()
        headers["Accept"] = "text/event-stream"

        logger.info(f"SSE connecting to {url} (configVersion={local_version})")

        # Use a separate client with longer timeout for SSE streaming
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=DEFAULT_HTTP_TIMEOUT,
                read=SSE_STREAM_TIMEOUT,
                write=DEFAULT_HTTP_TIMEOUT,
                pool=DEFAULT_HTTP_TIMEOUT,
            ),
            verify=config.get("api.verify_ssl", True),
        ) as sse_client:
            async with sse_client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    logger.error(f"SSE connection failed: {response.status_code}")
                    return

                logger.info("SSE connected")

                # SSE parsing state
                event_type = None
                event_data = ""
                event_id = None

                async for line in response.aiter_lines():
                    if not self._sse_running:
                        return

                    # Empty line signals end of an event
                    if line == "":
                        if event_type and event_data:
                            await self._handle_sse_event(event_type, event_data.strip(), event_id)
                        event_type = None
                        event_data = ""
                        event_id = None
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_part = line[5:].strip()
                        if event_data:
                            event_data += "\n" + data_part
                        else:
                            event_data = data_part
                    elif line.startswith("id:"):
                        event_id = line[3:].strip()
                    elif line.startswith(":"):
                        # SSE comment, ignore
                        pass

        logger.info("SSE stream ended")

    async def _handle_sse_event(self, event_type: str, data: str, event_id: Optional[str]):
        """Handle a parsed SSE event."""
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.error(f"SSE event '{event_type}' has invalid JSON: {data[:200]}")
            return

        if event_type == "config":
            await self._handle_config_event(payload)
        elif event_type == "action":
            await self._handle_action_event(payload)
        elif event_type == "heartbeat":
            await self._handle_heartbeat_event(payload)
        elif event_type == "connected":
            await self._handle_connected_event(payload)
        else:
            logger.debug(f"Unknown SSE event type: {event_type}")

    async def _handle_config_event(self, payload: dict):
        """Handle a config SSE event - full config push from API."""
        version = payload.get("configVersion", 0)
        logger.info(f"SSE received config event (version={version})")

        # Save to local store
        config_store.save_full_config(payload, version)

        # Extract device assignments (display-only; command routing always
        # goes by entity_id via registry.get_device, never by role).
        raw_assignments = payload.get("deviceAssignments", [])
        if not isinstance(raw_assignments, list):
            logger.warning(
                "deviceAssignments in config event is not a list (got %s); " "treating as empty",
                type(raw_assignments).__name__,
            )
            raw_assignments = []
        config_store.save_device_assignments(raw_assignments, version)

        # Extract settings and call the settings callback
        settings = {
            "rdh_mode": payload.get("rdhMode", False),
            "status": payload.get("status", ""),
            "light": payload.get("light", {}),
            "climate": payload.get("climate", {}),
            "tank": payload.get("tank", {}),
        }

        if self._settings_callback:
            try:
                await self._settings_callback(settings)
                logger.debug("Settings callback executed from SSE config event")
            except Exception as e:
                logger.error(f"Error in settings callback: {e}")

    async def _handle_action_event(self, payload: dict):
        """Handle an action SSE event - put action into command queue."""
        action_id = payload.get("id")
        action_type = payload.get("type")

        if not action_id:
            logger.warning(f"SSE action event missing id: {payload}")
            return

        logger.info(f"SSE received action event: {action_id} ({action_type})")

        # Put the action into the command queue for processing by _command_execution_task
        if self._command_queue:
            await self._command_queue.put(payload)

    async def _handle_heartbeat_event(self, payload: dict):
        """Handle a heartbeat SSE event - check configVersion for drift."""
        remote_version = payload.get("configVersion", 0)
        local_version = config_store.get_config_version()

        if remote_version != local_version:
            logger.info(
                f"SSE heartbeat: config version mismatch "
                f"(local={local_version}, remote={remote_version}), fetching full config"
            )
            full_config = await self.fetch_full_config()
            if full_config and self._settings_callback:
                settings = {
                    "rdh_mode": full_config.get("rdhMode", False),
                    "status": full_config.get("status", ""),
                    "light": full_config.get("light", {}),
                    "climate": full_config.get("climate", {}),
                    "tank": full_config.get("tank", {}),
                }
                try:
                    await self._settings_callback(settings)
                except Exception as e:
                    logger.error(f"Error in settings callback after heartbeat resync: {e}")
        else:
            logger.debug(f"SSE heartbeat: config version OK ({local_version})")

        # Manifest-hash drift detection: API echoes the hash it last accepted
        # in the heartbeat; if it diverges from what we last pushed, re-push.
        remote_manifest_hash = payload.get("manifestHash")
        if remote_manifest_hash:
            local_manifest_hash = config_store.get_manifest_hash()
            if remote_manifest_hash != local_manifest_hash:
                logger.info(
                    "SSE heartbeat: manifest hash mismatch "
                    f"(local={local_manifest_hash}, remote={remote_manifest_hash}), "
                    "scheduling re-push"
                )
                asyncio.create_task(self.send_manifest())

    async def _handle_connected_event(self, payload: dict):
        """Handle a connected SSE event - log and store version."""
        version = payload.get("configVersion", 0)
        logger.info(f"SSE connected event: configVersion={version}")

    # ─── State ──────────────────────────────────────────────────────

    def get_init_state(self) -> dict[str, bool]:
        """Get the initialization state of the API client."""
        return {
            "initialized": self._client is not None,
            "has_command_queue": self._command_queue is not None,
            "sse_running": self._sse_running,
        }


# Create a global instance for easy imports
api_client = ApiClient()

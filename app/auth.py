"""Authentication with the GrowAssistant Spring API."""

import asyncio
import json
import logging
import os
import uuid
from typing import Literal, Optional

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import config
from app.constants import (
    AUTH_POLL_INTERVAL,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_BACKOFF,
    DEFAULT_RETRY_MIN_BACKOFF,
    SPACE_CREATION_POLL_INTERVAL,
)
from app.utils.http_utils import build_auth_headers
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class AuthManager(metaclass=SingletonMeta):
    """Authentication manager for the GrowAssistant Spring API.

    Handles client registration, authentication, and credential management.
    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the authentication manager."""
        self._client: Optional[httpx.AsyncClient] = None
        self._base_url = config.get("api.url", "http://localhost:8080")

        data_dir = config.get("general.data_dir", "data")
        os.makedirs(data_dir, exist_ok=True)
        self._credentials_file = os.path.join(data_dir, "credentials.json")

        self._credentials: Optional[dict] = None
        self._auth_code: Optional[str] = None
        self._client_id: Optional[str] = None
        self._connection_timed_out: bool = False

        logger.info("Authentication manager initialized")

    async def start(self):
        """Start the authentication manager and load saved credentials."""
        self._client = httpx.AsyncClient(
            timeout=config.get("api.timeout", DEFAULT_HTTP_TIMEOUT),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=config.get("api.verify_ssl", True),
        )
        self._load_credentials()
        logger.info("Authentication manager started")

    async def stop(self):
        """Stop the authentication manager."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Authentication manager stopped")

    def _load_credentials(self) -> bool:
        """Load saved credentials from file."""
        if not os.path.exists(self._credentials_file):
            logger.info("No saved credentials found")
            return False

        try:
            with open(self._credentials_file) as f:
                self._credentials = json.load(f)
            self._client_id = self._credentials.get("client_id")
            logger.info(f"Loaded credentials for client ID: {self._client_id}")
            return True
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")
            return False

    def _save_credentials(self) -> bool:
        """Save credentials to file."""
        if not self._credentials:
            logger.warning("No credentials to save")
            return False

        try:
            with open(self._credentials_file, "w") as f:
                json.dump(self._credentials, f)
            logger.info(f"Saved credentials for client ID: {self._client_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving credentials: {e}")
            return False

    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        return self._credentials is not None and self._client_id is not None

    async def validate_credentials(self) -> bool:
        """Validate saved credentials with the API."""
        if not self.is_authenticated() or not self._client:
            logger.warning("Not authenticated or client not started")
            return False

        try:
            response = await self._client.get(
                f"{self._base_url}/client/{self._client_id}",
                headers=self._get_auth_headers(),
            )
            if response.status_code == 200:
                logger.info("Credentials validated successfully")
                return True
            logger.warning(f"Credentials validation failed: {response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Error validating credentials: {e}")
            return False

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for API requests."""
        token = self._credentials.get("token") if self._credentials else None
        return build_auth_headers(token=token)

    async def register_client(self) -> bool:
        """Register a new client with the API."""
        if not self._client:
            logger.error("Authentication manager not started")
            return False

        custom_id = self._generate_custom_id()
        url = f"{self._base_url}/client"

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
                    logger.info(f"Registering client with customId: {custom_id}")
                    response = await self._client.post(url, json={"customId": custom_id})
                    response.raise_for_status()

            response_data = response.json()
            self._client_id = response_data.get("id")
            self._auth_code = response_data.get("code")

            self._credentials = {
                "client_id": self._client_id,
                "custom_id": custom_id,
                "registration_time": str(asyncio.get_event_loop().time()),
            }
            self._save_credentials()

            logger.info(f"Client registered with ID: {self._client_id}")
            logger.info(f"Authentication code: {self._auth_code}")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error registering client: {e.response.status_code} - {e.response.text}"
            )
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            logger.error(f"Error registering client: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error registering client: {e}")

        return False

    def _generate_custom_id(self) -> str:
        """Generate a custom ID combining hostname and UUID."""
        hostname = (
            os.uname().nodename
            if hasattr(os, "uname")
            else os.environ.get("COMPUTERNAME", "unknown")
        )
        return f"{hostname}-{uuid.uuid4().hex[:8]}"

    def get_auth_code(self) -> Optional[str]:
        """Get the authentication code for connecting to the app."""
        return self._auth_code

    def is_connection_timed_out(self) -> bool:
        """Check if the connection polling has timed out."""
        return self._connection_timed_out

    def set_connection_timed_out(self, timed_out: bool) -> None:
        """Set the connection timeout state."""
        self._connection_timed_out = timed_out
        if timed_out:
            logger.info("Connection polling timed out")
        else:
            logger.info("Connection timeout state cleared")

    async def request_new_code(self) -> bool:
        """Request a new authentication code by re-registering the client.

        This clears the timeout state and registers a new client with the API.
        Returns True if successful, False otherwise.
        """
        logger.info("Requesting new authentication code...")
        self._connection_timed_out = False
        self._auth_code = None

        # Clear existing credentials to force re-registration
        self._credentials = None
        self._client_id = None

        # Delete the credentials file if it exists
        if os.path.exists(self._credentials_file):
            try:
                os.remove(self._credentials_file)
                logger.info("Removed old credentials file")
            except Exception as e:
                logger.error(f"Error removing credentials file: {e}")

        # Register a new client
        return await self.register_client()

    def display_auth_code(self) -> None:
        """Display the authentication code in a user-friendly format."""
        if not self._auth_code:
            print("\nNo authentication code available. Please register first.\n")
            return

        print(f"""
{"=" * 40}
    AUTHENTICATION CODE
{"=" * 40}

        {self._auth_code}

Enter this code in the GrowAssistant app
to connect this client to your environment.

{"=" * 40}
""")

    async def check_connection_status(
        self,
    ) -> tuple[bool, Literal["not_connected", "connected", "ready"]]:
        """Check if client has been connected to an environment.

        Returns:
            Tuple of (connected, status) where status is "not_connected", "connected", or "ready".
        """
        if not self._client_id or not self._client:
            logger.warning("No client ID or client not started")
            return False, "not_connected"

        try:
            response = await self._client.get(f"{self._base_url}/client/{self._client_id}")

            if response.status_code == 204:
                logger.info("Client connected to API but no space created yet")
                self._credentials["connected"] = True
                self._auth_code = None
                self._save_credentials()
                return True, "connected"

            if response.status_code == 200:
                logger.info("Client connected to environment with space created")
                self._credentials["connected"] = True
                self._credentials["ready"] = True
                self._auth_code = None
                self._save_credentials()
                response.json()  # Process response if needed
                return True, "ready"

            logger.warning(f"Error checking connection status: {response.status_code}")
            return False, "not_connected"

        except Exception as e:
            logger.error(f"Error checking connection status: {e}")
            return False, "not_connected"

    async def wait_for_connection(self, timeout: Optional[float] = None) -> bool:
        """Wait for the client to be connected to an environment."""
        start_time = asyncio.get_event_loop().time()
        self._connection_timed_out = False

        while True:
            connected, _ = await self.check_connection_status()
            if connected:
                self._connection_timed_out = False
                return True

            if timeout is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Connection timeout after {elapsed:.1f} seconds")
                    self._connection_timed_out = True
                    return False

            await asyncio.sleep(AUTH_POLL_INTERVAL)

    async def wait_for_space_creation(self, timeout: Optional[float] = None) -> bool:
        """Wait for a space to be created for this client (status code 200)."""
        start_time = asyncio.get_event_loop().time()

        while True:
            _, status = await self.check_connection_status()
            if status == "ready":
                logger.info("Space created successfully, client ready to send data")
                return True

            if timeout is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Space creation timeout after {elapsed:.1f} seconds")
                    return False

            logger.info(f"Space not created yet, checking in {SPACE_CREATION_POLL_INTERVAL}s")
            await asyncio.sleep(SPACE_CREATION_POLL_INTERVAL)

    def is_ready_for_data(self) -> bool:
        """Check if the client is ready to send data (connected and space created)."""
        return (
            self._credentials is not None
            and self._client_id is not None
            and self._credentials.get("ready", False)
        )

    def get_client_id(self) -> Optional[str]:
        """Get the client ID."""
        return self._client_id


# Create a global instance for easy imports
auth_manager = AuthManager()

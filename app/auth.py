"""Authentication & pairing with the GrowAssistant app (MQTT transport).

Pairing direction is reversed from the old SSE-era flow: the APP issues a
pairing code (shown in its UI), the operator enters it into this bridge's web
UI, and the bridge claims it over an HTTPS bootstrap call. There is no longer a
bridge-minted auth code, no self-registration, and no separate "space" gate —
once paired, the bridge is ready.
"""

import asyncio
import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import config
from app.constants import DEFAULT_HTTP_TIMEOUT
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

# Credential keys persisted to data/credentials.json.
CREDENTIAL_KEYS = ("bridgeId", "tenantId", "bridgeSecret", "token", "brokerUrl")

# Refresh the MQTT token once this fraction of its lifetime remains ahead of us
# — i.e. proactively at ~90% of the TTL, so rotation is seamless and never waits
# for a "Not authorized" CONNACK. The app hands us ``tokenExpiresIn`` (24h) on
# pair and refresh; the reactive CONNACK path stays as a fallback.
TOKEN_REFRESH_FRACTION = 0.9
# TTL assumed when a token/refresh response omits ``tokenExpiresIn`` (keeps the
# proactive loop from busy-spinning on a missing field).
DEFAULT_TOKEN_TTL_SECONDS = 24 * 60 * 60
# How often the proactive loop re-checks while unpaired (no token to schedule).
UNPAIRED_RECHECK_INTERVAL = 60.0
# Delay before retrying a proactive refresh that failed (app briefly down, etc.).
REFRESH_RETRY_INTERVAL = 60.0


class AuthManager(metaclass=SingletonMeta):
    """Pairing & credential manager for the GrowAssistant app.

    Handles the HTTPS pairing bootstrap, JWT token rotation, and persistence of
    the MQTT credentials. Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the authentication manager."""
        self._client: Optional[httpx.AsyncClient] = None
        self._base_url = config.get("api.url", "http://localhost:3000").rstrip("/")

        data_dir = config.get("general.data_dir", "data")
        os.makedirs(data_dir, exist_ok=True)
        self._credentials_file = os.path.join(data_dir, "credentials.json")

        self._credentials: Optional[dict] = None

        # Proactive-refresh state. ``_token_expires_in`` is the TTL (seconds) of
        # the current token, learned from the pair/refresh response; it is None
        # only before the first token is obtained or when creds are loaded from
        # disk (which triggers one refresh-now to relearn it).
        self._running: bool = False
        self._token_expires_in: Optional[int] = None
        self._refresh_loop_task: Optional[asyncio.Task] = None
        self._refresh_inflight: Optional[asyncio.Task] = None

        logger.info("Authentication manager initialized")

    async def start(self):
        """Start the authentication manager and load saved credentials."""
        self._client = httpx.AsyncClient(
            timeout=config.get("api.timeout", DEFAULT_HTTP_TIMEOUT),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=config.get("api.verify_ssl", True),
        )
        self._load_credentials()
        self._running = True
        self._refresh_loop_task = asyncio.create_task(self._proactive_refresh_loop())
        logger.info("Authentication manager started")

    async def stop(self):
        """Stop the authentication manager."""
        self._running = False
        if self._refresh_loop_task and not self._refresh_loop_task.done():
            self._refresh_loop_task.cancel()
            try:
                await self._refresh_loop_task
            except asyncio.CancelledError:
                pass
            self._refresh_loop_task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Authentication manager stopped")

    # ─── Credential persistence ─────────────────────────────────────

    def _load_credentials(self) -> bool:
        """Load saved credentials from file.

        Migrates gracefully: an old SSE-era file keyed by ``client_id`` is
        treated as unpaired (the bridge must be re-paired with the app).
        """
        if not os.path.exists(self._credentials_file):
            logger.info("No saved credentials found — bridge is unpaired")
            return False

        try:
            with open(self._credentials_file) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")
            return False

        if "bridgeId" not in data:
            # Legacy credentials (client_id-based) — treat as unpaired.
            logger.warning("Found legacy credentials without bridgeId — treating as unpaired")
            return False

        self._credentials = data
        logger.info(f"Loaded credentials for bridge ID: {data.get('bridgeId')}")
        return True

    def _save_credentials(self) -> bool:
        """Save credentials to file."""
        if not self._credentials:
            logger.warning("No credentials to save")
            return False

        try:
            with open(self._credentials_file, "w") as f:
                json.dump(self._credentials, f)
            logger.info(f"Saved credentials for bridge ID: {self._credentials.get('bridgeId')}")
            return True
        except Exception as e:
            logger.error(f"Error saving credentials: {e}")
            return False

    # ─── Pairing & token rotation ───────────────────────────────────

    async def pair_with_code(self, code: str, name: Optional[str] = None) -> bool:
        """Claim an app-issued pairing code over the HTTPS bootstrap.

        POSTs ``/api/bridge/pair`` with the code and this host's name. On
        success persists ``{bridgeId, tenantId, bridgeSecret, token,
        brokerUrl}`` and returns True. Returns False on bad/used codes
        (404/400) or transport errors.
        """
        if not self._client:
            logger.error("Authentication manager not started")
            return False

        if not code:
            logger.warning("pair_with_code called with empty code")
            return False

        hostname = name or self._hostname()
        url = f"{self._base_url}/api/bridge/pair"

        try:
            response = await self._client.post(url, json={"code": code, "name": hostname})
        except httpx.HTTPError as e:
            logger.error(f"Error pairing with code: {e}")
            return False

        if response.status_code != 200:
            try:
                err = response.json().get("error", response.text)
            except ValueError:
                err = response.text
            logger.warning(f"Pairing failed ({response.status_code}): {err}")
            return False

        data = response.json()
        self._credentials = {
            "bridgeId": data.get("bridgeId"),
            "tenantId": data.get("tenantId"),
            "bridgeSecret": data.get("bridgeSecret"),
            "token": data.get("token"),
            "brokerUrl": data.get("brokerUrl"),
        }
        self._token_expires_in = self._parse_expires_in(data)
        self._save_credentials()
        logger.info(f"Paired successfully as bridge {self._credentials.get('bridgeId')}")
        return True

    async def refresh_token(self) -> bool:
        """Rotate the JWT, coalescing concurrent callers.

        The proactive-refresh loop and the reactive CONNACK-failure path
        (``mqtt_transport``) can both fire near-simultaneously; a single
        in-flight refresh is shared so we never mint two tokens or race the
        credentials file.
        """
        inflight = self._refresh_inflight
        if inflight is not None and not inflight.done():
            return await inflight

        task = asyncio.ensure_future(self._do_refresh_token())
        self._refresh_inflight = task
        try:
            return await task
        finally:
            if self._refresh_inflight is task:
                self._refresh_inflight = None

    async def _do_refresh_token(self) -> bool:
        """Rotate the JWT using the stored bridgeId + bridgeSecret.

        POSTs ``/api/bridge/token`` and updates the stored token (and TTL) on
        success. Returns False on a bad secret (401) or transport errors.
        """
        if not self._client:
            logger.error("Authentication manager not started")
            return False

        bridge_id = self.get_client_id()
        bridge_secret = self.get_bridge_secret()
        if not bridge_id or not bridge_secret:
            logger.warning("Cannot refresh token: bridge is not paired")
            return False

        url = f"{self._base_url}/api/bridge/token"

        try:
            response = await self._client.post(
                url, json={"bridgeId": bridge_id, "bridgeSecret": bridge_secret}
            )
        except httpx.HTTPError as e:
            logger.error(f"Error refreshing token: {e}")
            return False

        if response.status_code != 200:
            logger.warning(f"Token refresh failed ({response.status_code})")
            return False

        data = response.json()
        token = data.get("token")
        if not token:
            logger.warning("Token refresh response missing token")
            return False

        self._credentials["token"] = token
        self._token_expires_in = self._parse_expires_in(data)
        self._save_credentials()
        logger.info("Token refreshed successfully")
        return True

    @staticmethod
    def _parse_expires_in(data: dict) -> int:
        """Read ``tokenExpiresIn`` (seconds) from a pair/refresh response.

        Falls back to the default TTL when the field is missing or invalid, so
        the proactive loop always has a positive interval to schedule against.
        """
        value = data.get("tokenExpiresIn")
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        return DEFAULT_TOKEN_TTL_SECONDS

    # ─── Proactive token refresh ────────────────────────────────────

    async def _proactive_refresh_loop(self) -> None:
        """Refresh the token at ~90% of its TTL, ahead of broker rejection.

        Sleeps until the next scheduled refresh, rotates, and reschedules off
        the fresh TTL. While unpaired it re-checks periodically; on a failed
        refresh it retries sooner than a full TTL. The reactive CONNACK path
        remains the fallback if this ever misses the window.
        """
        logger.info("Proactive token-refresh loop started")
        try:
            next_delay = self._initial_refresh_delay()
            while self._running:
                await asyncio.sleep(next_delay)
                if not self._running:
                    break
                if not self.is_authenticated():
                    next_delay = UNPAIRED_RECHECK_INTERVAL
                    continue
                if await self.refresh_token():
                    next_delay = self._scheduled_refresh_delay()
                else:
                    next_delay = REFRESH_RETRY_INTERVAL
        except asyncio.CancelledError:
            pass
        logger.info("Proactive token-refresh loop stopped")

    def _initial_refresh_delay(self) -> float:
        """Delay before the loop's first action.

        Unpaired → re-check interval. Paired with an unknown TTL (creds loaded
        from disk) → 0, so we refresh immediately to relearn the lifetime.
        """
        if not self.is_authenticated():
            return UNPAIRED_RECHECK_INTERVAL
        if self._token_expires_in is None:
            return 0.0
        return self._scheduled_refresh_delay()

    def _scheduled_refresh_delay(self) -> float:
        """Seconds to wait after minting a token before refreshing it (~90% TTL)."""
        ttl = self._token_expires_in or DEFAULT_TOKEN_TTL_SECONDS
        return max(0.0, ttl * TOKEN_REFRESH_FRACTION)

    async def fetch_ice_servers(self) -> Optional[list]:
        """Fetch go2rtc's WebRTC ICE servers (STUN + TURN) from the app.

        POSTs ``/api/bridge/ice-servers`` with the stored bridgeId + bridgeSecret
        (same auth as token rotation). The app mints a long-TTL TURN credential
        server-side, so the TURN shared secret never lives on the bridge.

        Returns the ICE servers list (possibly empty if the app has no TURN/STUN
        configured), or ``None`` on an auth/transport failure — the camera
        integration then proceeds without relay candidates (host-only P2P).
        """
        if not self._client:
            logger.error("Authentication manager not started")
            return None

        bridge_id = self.get_client_id()
        bridge_secret = self.get_bridge_secret()
        if not bridge_id or not bridge_secret:
            logger.warning("Cannot fetch ICE servers: bridge is not paired")
            return None

        url = f"{self._base_url}/api/bridge/ice-servers"

        try:
            response = await self._client.post(
                url, json={"bridgeId": bridge_id, "bridgeSecret": bridge_secret}
            )
        except httpx.HTTPError as e:
            logger.error(f"Error fetching ICE servers: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"ICE servers fetch failed ({response.status_code})")
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning("ICE servers response was not JSON")
            return None

        ice_servers = data.get("iceServers")
        if not isinstance(ice_servers, list):
            logger.warning("ICE servers response missing iceServers list")
            return None

        logger.info(f"Fetched {len(ice_servers)} ICE server(s) for go2rtc")
        return ice_servers

    def _hostname(self) -> str:
        """Return this host's name for the pairing call."""
        if hasattr(os, "uname"):
            return os.uname().nodename
        return os.environ.get("COMPUTERNAME", "unknown")

    # ─── State ──────────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        """Check if the bridge is paired (creds present)."""
        if not self._credentials:
            return False
        return all(self._credentials.get(k) for k in ("bridgeId", "token", "bridgeSecret"))

    def is_ready_for_data(self) -> bool:
        """Check if the bridge may send data.

        MQTT pairing implies ready — there is no separate "space" gate, so this
        is identical to ``is_authenticated()``. Kept as a distinct name because
        main.py and the web layer call it.
        """
        return self.is_authenticated()

    # ─── Getters ────────────────────────────────────────────────────

    def get_client_id(self) -> Optional[str]:
        """Return the bridge ID (the stable MQTT client id)."""
        return self._credentials.get("bridgeId") if self._credentials else None

    def get_tenant_id(self) -> Optional[str]:
        """Return the tenant ID."""
        return self._credentials.get("tenantId") if self._credentials else None

    def get_token(self) -> Optional[str]:
        """Return the current JWT (used as the MQTT password)."""
        return self._credentials.get("token") if self._credentials else None

    def get_broker_url(self) -> Optional[str]:
        """Return the broker URL delivered at pairing (e.g. mqtt://host:1883)."""
        return self._credentials.get("brokerUrl") if self._credentials else None

    def get_bridge_secret(self) -> Optional[str]:
        """Return the bridge secret used for token rotation."""
        return self._credentials.get("bridgeSecret") if self._credentials else None

    def get_broker_host_port(self, default_port: int = 1883) -> tuple[Optional[str], int]:
        """Parse host/port from the stored broker URL.

        Returns ``(host, port)``; host is None when no broker URL is stored.
        """
        broker_url = self.get_broker_url()
        if not broker_url:
            return None, default_port
        parsed = urlparse(broker_url)
        host = parsed.hostname
        port = parsed.port or default_port
        return host, port


# Create a global instance for easy imports
auth_manager = AuthManager()

"""Tests for the AuthManager module (MQTT-era pairing flow).

Covers lifecycle, credential persistence (including graceful migration of an
old client_id-keyed file), the state getters, pairing via pair_with_code, token
rotation via refresh_token, and broker-URL parsing. httpx is mocked — no live
app is required.
"""

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.fixture
def auth_manager(mock_config, tmp_path):
    """Create a fresh AuthManager instance with an isolated data dir."""
    mock_config.get.side_effect = lambda key, default=None: {
        "api.url": "http://localhost:3000",
        "general.data_dir": str(tmp_path / "data"),
        "api.verify_ssl": True,
        "api.timeout": 30,
    }.get(key, default)

    with patch("app.auth.config", mock_config):
        from app.auth import AuthManager
        from app.utils.singleton import SingletonMeta

        if AuthManager in SingletonMeta._instances:
            del SingletonMeta._instances[AuthManager]

        manager = AuthManager()
        yield manager

        if AuthManager in SingletonMeta._instances:
            del SingletonMeta._instances[AuthManager]


def _response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


class TestAuthManagerLifecycle:
    """Tests for AuthManager start/stop and initialization."""

    def test_initialization(self, auth_manager):
        """Initialization wires the base URL and starts unpaired."""
        assert auth_manager._base_url == "http://localhost:3000"
        assert auth_manager._credentials is None
        assert auth_manager.is_authenticated() is False

    @pytest.mark.asyncio
    async def test_start_creates_client(self, auth_manager):
        """start creates the HTTP client."""
        mock_client = AsyncMock()
        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            await auth_manager.start()
            assert auth_manager._client is mock_client
            await auth_manager.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_client(self, auth_manager):
        """stop closes the HTTP client."""
        mock_client = AsyncMock()
        auth_manager._client = mock_client
        await auth_manager.stop()
        mock_client.aclose.assert_called_once()
        assert auth_manager._client is None


class TestAuthManagerCredentials:
    """Tests for credential persistence and state."""

    def test_load_credentials_no_file(self, auth_manager):
        """Loading with no file present returns False and stays unpaired."""
        assert auth_manager._load_credentials() is False
        assert auth_manager.is_authenticated() is False

    def test_load_credentials_success(self, auth_manager):
        """A valid credentials file is loaded."""
        creds = {
            "bridgeId": "b1",
            "tenantId": "t1",
            "bridgeSecret": "s1",
            "token": "jwt1",
            "brokerUrl": "mqtt://broker:1883",
        }
        with open(auth_manager._credentials_file, "w") as f:
            json.dump(creds, f)

        assert auth_manager._load_credentials() is True
        assert auth_manager.get_client_id() == "b1"
        assert auth_manager.is_authenticated() is True

    def test_legacy_credentials_treated_as_unpaired(self, auth_manager):
        """An old client_id-keyed file is treated as unpaired."""
        with open(auth_manager._credentials_file, "w") as f:
            json.dump({"client_id": "legacy", "custom_id": "host-x"}, f)

        assert auth_manager._load_credentials() is False
        assert auth_manager.is_authenticated() is False

    def test_save_credentials(self, auth_manager):
        """Saving persists the credentials dict to disk."""
        auth_manager._credentials = {
            "bridgeId": "b1",
            "tenantId": "t1",
            "bridgeSecret": "s1",
            "token": "jwt1",
            "brokerUrl": "mqtt://broker:1883",
        }
        assert auth_manager._save_credentials() is True
        with open(auth_manager._credentials_file) as f:
            assert json.load(f)["bridgeId"] == "b1"

    def test_save_credentials_none(self, auth_manager):
        """Saving with no credentials returns False."""
        auth_manager._credentials = None
        assert auth_manager._save_credentials() is False

    def test_is_authenticated_requires_all_keys(self, auth_manager):
        """is_authenticated requires bridgeId, token AND bridgeSecret."""
        auth_manager._credentials = {"bridgeId": "b1", "token": "jwt1"}
        assert auth_manager.is_authenticated() is False
        auth_manager._credentials["bridgeSecret"] = "s1"
        assert auth_manager.is_authenticated() is True

    def test_is_ready_for_data_equals_authenticated(self, auth_manager):
        """is_ready_for_data mirrors is_authenticated (no separate gate)."""
        auth_manager._credentials = {
            "bridgeId": "b1",
            "token": "jwt1",
            "bridgeSecret": "s1",
        }
        assert auth_manager.is_ready_for_data() is True
        auth_manager._credentials = None
        assert auth_manager.is_ready_for_data() is False


class TestAuthManagerGetters:
    """Tests for the credential getters."""

    def test_getters(self, auth_manager):
        auth_manager._credentials = {
            "bridgeId": "b1",
            "tenantId": "t1",
            "bridgeSecret": "s1",
            "token": "jwt1",
            "brokerUrl": "mqtt://broker:1883",
        }
        assert auth_manager.get_client_id() == "b1"
        assert auth_manager.get_tenant_id() == "t1"
        assert auth_manager.get_bridge_secret() == "s1"
        assert auth_manager.get_token() == "jwt1"
        assert auth_manager.get_broker_url() == "mqtt://broker:1883"

    def test_getters_when_unpaired(self, auth_manager):
        assert auth_manager.get_client_id() is None
        assert auth_manager.get_tenant_id() is None
        assert auth_manager.get_token() is None
        assert auth_manager.get_broker_url() is None
        assert auth_manager.get_bridge_secret() is None

    def test_broker_host_port_parsing(self, auth_manager):
        auth_manager._credentials = {"brokerUrl": "mqtt://broker.local:8883"}
        assert auth_manager.get_broker_host_port() == ("broker.local", 8883)

        auth_manager._credentials = {"brokerUrl": "mqtt://broker.local"}
        assert auth_manager.get_broker_host_port(1883) == ("broker.local", 1883)

        auth_manager._credentials = None
        assert auth_manager.get_broker_host_port() == (None, 1883)


class TestAuthManagerPairing:
    """Tests for pair_with_code."""

    @pytest.mark.asyncio
    async def test_pair_with_code_success(self, auth_manager):
        """A 200 pairing response stores all five keys and returns True."""
        auth_manager._client = MagicMock()
        body = {
            "bridgeId": "bridge-1",
            "tenantId": "tenant-1",
            "bridgeSecret": "secret-1",
            "token": "jwt-1",
            "tokenExpiresIn": 86400,
            "brokerUrl": "mqtt://broker.local:1883",
        }
        auth_manager._client.post = AsyncMock(return_value=_response(200, body))

        assert await auth_manager.pair_with_code("ABC123", name="pi-host") is True
        assert auth_manager.is_authenticated() is True
        assert auth_manager.get_token() == "jwt-1"

        with open(auth_manager._credentials_file) as f:
            saved = json.load(f)
        assert set(saved) == {"bridgeId", "tenantId", "bridgeSecret", "token", "brokerUrl"}

        _, kwargs = auth_manager._client.post.call_args
        assert kwargs["json"] == {"code": "ABC123", "name": "pi-host"}

    @pytest.mark.asyncio
    async def test_pair_with_code_404(self, auth_manager):
        """A 404 returns False and stores nothing."""
        auth_manager._client = MagicMock()
        auth_manager._client.post = AsyncMock(return_value=_response(404, {"error": "bad code"}))

        assert await auth_manager.pair_with_code("BADCODE") is False
        assert auth_manager.is_authenticated() is False

    @pytest.mark.asyncio
    async def test_pair_with_code_empty(self, auth_manager):
        """An empty code returns False without calling the API."""
        auth_manager._client = MagicMock()
        auth_manager._client.post = AsyncMock()
        assert await auth_manager.pair_with_code("") is False
        auth_manager._client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_pair_with_code_transport_error(self, auth_manager):
        """A transport error returns False."""
        auth_manager._client = MagicMock()
        auth_manager._client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        assert await auth_manager.pair_with_code("ABC123") is False


class TestAuthManagerTokenRefresh:
    """Tests for refresh_token."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, auth_manager):
        """refresh_token updates the stored token using bridgeId + secret."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {
            "bridgeId": "b1",
            "bridgeSecret": "s1",
            "token": "old",
        }
        auth_manager._client.post = AsyncMock(
            return_value=_response(200, {"token": "new", "tokenExpiresIn": 86400})
        )

        assert await auth_manager.refresh_token() is True
        assert auth_manager.get_token() == "new"

        _, kwargs = auth_manager._client.post.call_args
        assert kwargs["json"] == {"bridgeId": "b1", "bridgeSecret": "s1"}

    @pytest.mark.asyncio
    async def test_refresh_token_401(self, auth_manager):
        """A 401 leaves the token unchanged and returns False."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "old"}
        auth_manager._client.post = AsyncMock(return_value=_response(401, {"error": "bad"}))

        assert await auth_manager.refresh_token() is False
        assert auth_manager.get_token() == "old"

    @pytest.mark.asyncio
    async def test_refresh_token_unpaired(self, auth_manager):
        """refresh_token returns False when not paired."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = None
        assert await auth_manager.refresh_token() is False

    @pytest.mark.asyncio
    async def test_concurrent_refresh_is_coalesced(self, auth_manager):
        """Proactive + reactive callers share a single in-flight refresh."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "old"}

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_post(*args, **kwargs):
            started.set()
            await release.wait()
            return _response(200, {"token": "new", "tokenExpiresIn": 86400})

        auth_manager._client.post = AsyncMock(side_effect=slow_post)

        first = asyncio.create_task(auth_manager.refresh_token())
        await started.wait()
        second = asyncio.create_task(auth_manager.refresh_token())
        release.set()

        assert await first is True
        assert await second is True
        # Only one network round-trip despite two callers.
        assert auth_manager._client.post.call_count == 1


class TestAuthManagerProactiveRefresh:
    """Tests for the proactive token-refresh scheduling."""

    def test_pair_captures_token_expires_in(self, auth_manager):
        assert auth_manager._parse_expires_in({"tokenExpiresIn": 86400}) == 86400

    def test_expires_in_defaults_when_missing(self, auth_manager):
        from app.auth import DEFAULT_TOKEN_TTL_SECONDS

        assert auth_manager._parse_expires_in({}) == DEFAULT_TOKEN_TTL_SECONDS
        assert auth_manager._parse_expires_in({"tokenExpiresIn": 0}) == DEFAULT_TOKEN_TTL_SECONDS

    def test_scheduled_delay_is_ninety_percent_of_ttl(self, auth_manager):
        auth_manager._token_expires_in = 1000
        assert auth_manager._scheduled_refresh_delay() == 900.0

    def test_initial_delay_unpaired_rechecks(self, auth_manager):
        from app.auth import UNPAIRED_RECHECK_INTERVAL

        auth_manager._credentials = None
        assert auth_manager._initial_refresh_delay() == UNPAIRED_RECHECK_INTERVAL

    def test_initial_delay_refreshes_now_when_ttl_unknown(self, auth_manager):
        # Paired (creds loaded from disk) but TTL not yet known → refresh now.
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._token_expires_in = None
        assert auth_manager._initial_refresh_delay() == 0.0

    @pytest.mark.asyncio
    async def test_loop_refreshes_before_expiry_with_short_ttl(self, auth_manager):
        """With a short TTL the loop rotates the token repeatedly, ahead of expiry."""
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._token_expires_in = None  # first iteration refreshes immediately

        async def fake_refresh():
            # Learn a tiny TTL so the next scheduled refresh fires quickly.
            auth_manager._token_expires_in = 0.02
            return True

        with patch.object(auth_manager, "_do_refresh_token", side_effect=fake_refresh) as mock:
            auth_manager._running = True
            task = asyncio.create_task(auth_manager._proactive_refresh_loop())
            await asyncio.sleep(0.1)
            auth_manager._running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Refreshed immediately, then again at ~90% of the short TTL.
        assert mock.call_count >= 2


class TestAuthManagerFetchIceServers:
    """Tests for fetch_ice_servers (go2rtc WebRTC ICE config)."""

    @pytest.mark.asyncio
    async def test_fetch_ice_servers_success(self, auth_manager):
        """Returns the iceServers list, authenticating with bridgeId + secret."""
        ice = [{"urls": ["turn:t:3478"], "username": "u", "credential": "c"}]
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._client.post = AsyncMock(return_value=_response(200, {"iceServers": ice}))

        assert await auth_manager.fetch_ice_servers() == ice
        args, kwargs = auth_manager._client.post.call_args
        assert args[0].endswith("/api/bridge/ice-servers")
        assert kwargs["json"] == {"bridgeId": "b1", "bridgeSecret": "s1"}

    @pytest.mark.asyncio
    async def test_fetch_ice_servers_empty_list(self, auth_manager):
        """An empty list (no TURN/STUN configured) is returned verbatim."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._client.post = AsyncMock(return_value=_response(200, {"iceServers": []}))
        assert await auth_manager.fetch_ice_servers() == []

    @pytest.mark.asyncio
    async def test_fetch_ice_servers_401(self, auth_manager):
        """A 401 returns None (camera proceeds host-only)."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._client.post = AsyncMock(return_value=_response(401, {"error": "bad"}))
        assert await auth_manager.fetch_ice_servers() is None

    @pytest.mark.asyncio
    async def test_fetch_ice_servers_malformed(self, auth_manager):
        """A response missing the iceServers list returns None."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = {"bridgeId": "b1", "bridgeSecret": "s1", "token": "t"}
        auth_manager._client.post = AsyncMock(return_value=_response(200, {"nope": 1}))
        assert await auth_manager.fetch_ice_servers() is None

    @pytest.mark.asyncio
    async def test_fetch_ice_servers_unpaired(self, auth_manager):
        """Returns None when not paired."""
        auth_manager._client = MagicMock()
        auth_manager._credentials = None
        assert await auth_manager.fetch_ice_servers() is None

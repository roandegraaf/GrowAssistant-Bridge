"""
Tests for the AuthManager module.

This module tests client registration, credential management,
connection status checking, and authentication flows.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestAuthManager:
    """Tests for the AuthManager class."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create a fresh AuthManager instance."""
        mock_config.get.side_effect = lambda key, default=None: {
            "api.url": "http://localhost:8080",
            "general.data_dir": str(tmp_path / "data"),
            "api.verify_ssl": True,
            "api.timeout": 30,
            "api.retry_max_attempts": 3,
            "api.retry_min_backoff": 1,
            "api.retry_max_backoff": 10,
        }.get(key, default)

        with patch("app.auth.config", mock_config):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            yield manager

    def test_initialization(self, auth_manager, tmp_path):
        """Test AuthManager initialization."""
        assert auth_manager._base_url == "http://localhost:8080"
        assert auth_manager._credentials is None
        assert auth_manager._client_id is None
        assert auth_manager._auth_code is None

    @pytest.mark.asyncio
    async def test_start_creates_client(self, auth_manager):
        """Test that start creates HTTP client."""
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            await auth_manager.start()

            assert auth_manager._client is mock_client

            await auth_manager.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_client(self, auth_manager):
        """Test that stop closes HTTP client."""
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        auth_manager._client = mock_client

        await auth_manager.stop()

        mock_client.aclose.assert_called_once()
        assert auth_manager._client is None


class TestAuthManagerCredentials:
    """Tests for AuthManager credential management."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create AuthManager with temp directory."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        mock_config.get.side_effect = lambda key, default=None: {
            "api.url": "http://localhost:8080",
            "general.data_dir": str(data_dir),
            "api.verify_ssl": True,
            "api.timeout": 30,
        }.get(key, default)

        with patch("app.auth.config", mock_config):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            manager._data_dir = str(data_dir)
            yield manager

    def test_load_credentials_no_file(self, auth_manager):
        """Test loading credentials when file doesn't exist."""
        result = auth_manager._load_credentials()

        assert result is False
        assert auth_manager._credentials is None

    def test_load_credentials_success(self, auth_manager, tmp_path):
        """Test loading credentials from file."""
        credentials = {
            "client_id": "test-client-123",
            "custom_id": "test-custom-id",
        }
        credentials_file = tmp_path / "data" / "credentials.json"
        credentials_file.parent.mkdir(exist_ok=True)

        with open(credentials_file, "w") as f:
            json.dump(credentials, f)

        auth_manager._credentials_file = str(credentials_file)

        result = auth_manager._load_credentials()

        assert result is True
        assert auth_manager._credentials == credentials
        assert auth_manager._client_id == "test-client-123"

    def test_save_credentials_success(self, auth_manager, tmp_path):
        """Test saving credentials to file."""
        auth_manager._credentials = {
            "client_id": "test-client-456",
            "custom_id": "test-custom-id",
        }
        credentials_file = tmp_path / "data" / "credentials.json"
        auth_manager._credentials_file = str(credentials_file)

        result = auth_manager._save_credentials()

        assert result is True
        assert credentials_file.exists()

        with open(credentials_file) as f:
            saved = json.load(f)

        assert saved["client_id"] == "test-client-456"

    def test_save_credentials_no_credentials(self, auth_manager):
        """Test saving when no credentials exist."""
        auth_manager._credentials = None

        result = auth_manager._save_credentials()

        assert result is False

    def test_is_authenticated_true(self, auth_manager):
        """Test is_authenticated returns True when authenticated."""
        auth_manager._credentials = {"client_id": "test-123"}
        auth_manager._client_id = "test-123"

        assert auth_manager.is_authenticated() is True

    def test_is_authenticated_false_no_credentials(self, auth_manager):
        """Test is_authenticated returns False without credentials."""
        assert auth_manager.is_authenticated() is False

    def test_is_authenticated_false_no_client_id(self, auth_manager):
        """Test is_authenticated returns False without client_id."""
        auth_manager._credentials = {"some": "data"}

        assert auth_manager.is_authenticated() is False

    def test_get_client_id(self, auth_manager):
        """Test getting client ID."""
        auth_manager._client_id = "client-789"

        assert auth_manager.get_client_id() == "client-789"

    def test_get_auth_code(self, auth_manager):
        """Test getting auth code."""
        auth_manager._auth_code = "ABC123"

        assert auth_manager.get_auth_code() == "ABC123"

    def test_is_ready_for_data(self, auth_manager):
        """Test checking if ready for data."""
        auth_manager._credentials = {"client_id": "test-123", "ready": True}
        auth_manager._client_id = "test-123"

        assert auth_manager.is_ready_for_data() is True

    def test_is_ready_for_data_not_ready(self, auth_manager):
        """Test not ready when connected but space not created."""
        auth_manager._credentials = {"client_id": "test-123", "connected": True}
        auth_manager._client_id = "test-123"

        assert auth_manager.is_ready_for_data() is False


class TestAuthManagerRegistration:
    """Tests for AuthManager client registration."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create AuthManager with mocked HTTP client."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        mock_config.get.side_effect = lambda key, default=None: {
            "api.url": "http://localhost:8080",
            "general.data_dir": str(data_dir),
            "api.verify_ssl": True,
            "api.timeout": 30,
            "api.retry_max_attempts": 1,
            "api.retry_min_backoff": 0.1,
            "api.retry_max_backoff": 0.2,
        }.get(key, default)

        with patch("app.auth.config", mock_config):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            manager._credentials_file = str(data_dir / "credentials.json")
            yield manager

    @pytest.mark.asyncio
    async def test_register_client_success(self, auth_manager):
        """Test successful client registration."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "new-client-id",
            "code": "AUTH123",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        auth_manager._client = mock_client

        result = await auth_manager.register_client()

        assert result is True
        assert auth_manager._client_id == "new-client-id"
        assert auth_manager._auth_code == "AUTH123"
        assert auth_manager._credentials is not None

    @pytest.mark.asyncio
    async def test_register_client_no_client(self, auth_manager):
        """Test registration fails without HTTP client."""
        auth_manager._client = None

        result = await auth_manager.register_client()

        assert result is False

    @pytest.mark.asyncio
    async def test_register_client_http_error(self, auth_manager):
        """Test registration handles HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        auth_manager._client = mock_client

        result = await auth_manager.register_client()

        assert result is False

    def test_generate_custom_id(self, auth_manager):
        """Test custom ID generation."""
        custom_id = auth_manager._generate_custom_id()

        assert isinstance(custom_id, str)
        assert "-" in custom_id
        assert len(custom_id) > 8


class TestAuthManagerConnectionStatus:
    """Tests for AuthManager connection status checking."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create AuthManager with mocked HTTP client."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        mock_config.get.side_effect = lambda key, default=None: {
            "api.url": "http://localhost:8080",
            "general.data_dir": str(data_dir),
            "api.verify_ssl": True,
            "api.timeout": 30,
        }.get(key, default)

        with patch("app.auth.config", mock_config):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            manager._credentials_file = str(data_dir / "credentials.json")
            manager._credentials = {}
            yield manager

    @pytest.mark.asyncio
    async def test_check_connection_status_not_connected(self, auth_manager):
        """Test connection status when not connected."""
        connected, status = await auth_manager.check_connection_status()

        assert connected is False
        assert status == "not_connected"

    @pytest.mark.asyncio
    async def test_check_connection_status_204(self, auth_manager):
        """Test connection status with 204 response (connected, no space)."""
        mock_response = MagicMock()
        mock_response.status_code = 204

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        auth_manager._client = mock_client
        auth_manager._client_id = "test-client"

        connected, status = await auth_manager.check_connection_status()

        assert connected is True
        assert status == "connected"

    @pytest.mark.asyncio
    async def test_check_connection_status_200(self, auth_manager):
        """Test connection status with 200 response (ready)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"space": "data"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        auth_manager._client = mock_client
        auth_manager._client_id = "test-client"

        connected, status = await auth_manager.check_connection_status()

        assert connected is True
        assert status == "ready"

    @pytest.mark.asyncio
    async def test_check_connection_status_error(self, auth_manager):
        """Test connection status on error."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Network error")

        auth_manager._client = mock_client
        auth_manager._client_id = "test-client"

        connected, status = await auth_manager.check_connection_status()

        assert connected is False
        assert status == "not_connected"


class TestAuthManagerWaiting:
    """Tests for AuthManager wait methods."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create AuthManager with mocked HTTP client."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        mock_config.get.side_effect = lambda key, default=None: {
            "api.url": "http://localhost:8080",
            "general.data_dir": str(data_dir),
        }.get(key, default)

        with patch("app.auth.config", mock_config), \
             patch("app.auth.AUTH_POLL_INTERVAL", 0.1), \
             patch("app.auth.SPACE_CREATION_POLL_INTERVAL", 0.1):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            manager._credentials_file = str(data_dir / "credentials.json")
            manager._credentials = {}
            yield manager

    @pytest.mark.asyncio
    async def test_wait_for_connection_success(self, auth_manager):
        """Test wait_for_connection succeeds when connected."""
        call_count = 0

        async def mock_check_status():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return True, "connected"
            return False, "not_connected"

        auth_manager.check_connection_status = mock_check_status

        result = await auth_manager.wait_for_connection(timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_connection_timeout(self, auth_manager):
        """Test wait_for_connection times out."""

        async def mock_check_status():
            return False, "not_connected"

        auth_manager.check_connection_status = mock_check_status

        result = await auth_manager.wait_for_connection(timeout=0.2)

        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_space_creation_success(self, auth_manager):
        """Test wait_for_space_creation succeeds when ready."""
        call_count = 0

        async def mock_check_status():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return True, "ready"
            return True, "connected"

        auth_manager.check_connection_status = mock_check_status

        result = await auth_manager.wait_for_space_creation(timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_space_creation_timeout(self, auth_manager):
        """Test wait_for_space_creation times out."""

        async def mock_check_status():
            return True, "connected"

        auth_manager.check_connection_status = mock_check_status

        result = await auth_manager.wait_for_space_creation(timeout=0.2)

        assert result is False


class TestAuthManagerDisplayAuthCode:
    """Tests for AuthManager display_auth_code method."""

    @pytest.fixture
    def auth_manager(self, mock_config, tmp_path):
        """Create AuthManager."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        mock_config.get.side_effect = lambda key, default=None: {
            "general.data_dir": str(data_dir),
        }.get(key, default)

        with patch("app.auth.config", mock_config):
            from app.auth import AuthManager
            from app.utils.singleton import SingletonMeta

            if AuthManager in SingletonMeta._instances:
                del SingletonMeta._instances[AuthManager]

            manager = AuthManager()
            yield manager

    def test_display_auth_code_with_code(self, auth_manager, capsys):
        """Test displaying auth code when available."""
        auth_manager._auth_code = "TEST123"

        auth_manager.display_auth_code()

        captured = capsys.readouterr()
        assert "TEST123" in captured.out
        assert "AUTHENTICATION CODE" in captured.out

    def test_display_auth_code_without_code(self, auth_manager, capsys):
        """Test displaying message when no code available."""
        auth_manager._auth_code = None

        auth_manager.display_auth_code()

        captured = capsys.readouterr()
        assert "No authentication code available" in captured.out

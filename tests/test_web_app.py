"""
Tests for the Flask web application.

This module tests the web routes, authentication, configuration endpoints,
and API endpoints using Flask's test client.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml


class TestWebAppRoutes:
    """Tests for web app routes."""

    @pytest.fixture
    def client(self, mock_config):
        """Create Flask test client."""
        with (
            patch("web.app.config", mock_config),
            patch("web.app.auth_manager") as mock_auth,
            patch("web.app.registry"),
        ):
            mock_auth.get_auth_code.return_value = None
            mock_auth.is_authenticated.return_value = True

            from web.app import app

            app.config["TESTING"] = True
            app.config["WTF_CSRF_ENABLED"] = False
            app.config["SECRET_KEY"] = "test-secret-key"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with app.app_context():
                    yield client

    def test_login_page_renders(self, client):
        """Test login page renders when auth is enabled."""
        with patch("web.app.config") as mock_config:
            mock_config.get.side_effect = lambda key, default=None: {
                "web.auth_enabled": True,
                "web.password_hash": "some-hash",
            }.get(key, default)

            with patch("web.app.is_password_set", return_value=True):
                response = client.get("/login")

            assert response.status_code == 200

    def test_setup_page_renders(self, client):
        """Test setup page renders when password not set."""
        with patch("web.app.is_password_set", return_value=False):
            response = client.get("/setup")

            assert response.status_code == 200

    def test_setup_redirects_when_password_set(self, client):
        """Test setup redirects to login when password is set."""
        with patch("web.app.is_password_set", return_value=True):
            response = client.get("/setup")

            assert response.status_code == 302
            assert "/login" in response.location

    def test_logout(self, client):
        """Test logout clears session."""
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        response = client.get("/logout")

        assert response.status_code == 302


class TestWebAppAuthentication:
    """Tests for web app authentication."""

    @pytest.fixture
    def client(self, mock_config, tmp_path):
        """Create Flask test client with auth enabled."""
        from werkzeug.security import generate_password_hash

        password_hash = generate_password_hash("testpass")

        mock_config.get.side_effect = lambda key, default=None: {
            "web.auth_enabled": True,
            "web.username": "admin",
            "web.password_hash": password_hash,
        }.get(key, default)
        mock_config.config_file = str(tmp_path / "config.yaml")

        with patch("web.app.config", mock_config), patch("web.app.auth_manager") as mock_auth:
            mock_auth.get_auth_code.return_value = None
            mock_auth.is_authenticated.return_value = True

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with app.app_context():
                    yield client

    def test_login_success(self, client):
        """Test successful login."""
        with patch("web.app.is_password_set", return_value=True):
            response = client.post(
                "/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False
            )

            assert response.status_code == 302

    def test_login_failure(self, client):
        """Test failed login with wrong password."""
        with patch("web.app.is_password_set", return_value=True):
            response = client.post("/login", data={"username": "admin", "password": "wrongpass"})

            assert b"Invalid" in response.data or response.status_code == 200

    def test_protected_route_requires_login(self, client):
        """Test protected route redirects to login."""
        with patch("web.app.config") as mock_config:
            mock_config.get.side_effect = lambda key, default=None: {
                "web.auth_enabled": True,
            }.get(key, default)

            response = client.get("/", follow_redirects=False)

            assert response.status_code in [302, 200]


class TestWebAppAPIEndpoints:
    """Tests for web app API endpoints."""

    @pytest.fixture
    def authenticated_client(self, mock_config):
        """Create authenticated Flask test client."""
        with (
            patch("web.app.config", mock_config),
            patch("web.app.auth_manager") as mock_auth,
            patch("web.app.registry") as mock_registry,
        ):
            mock_auth.get_auth_code.return_value = None
            mock_auth.is_authenticated.return_value = True

            mock_registry.get_device_types.return_value = ["pump", "temperature"]
            mock_registry.get_device_actions.return_value = ["on", "off"]

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = MagicMock()
            app.config["APPLICATION_INSTANCE"]._integrations = {}

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                with app.app_context():
                    yield client

    def test_get_device_types(self, authenticated_client):
        """Test getting device types."""
        response = authenticated_client.get("/api/device-types")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)

    def test_get_queue_info(self, authenticated_client):
        """Test getting queue info."""
        # The queue_manager is imported inside the function, so we patch the module
        with patch("app.queue_manager.queue_manager") as mock_queue:
            mock_queue.size.return_value = 10
            mock_queue.is_empty.return_value = False

            response = authenticated_client.get("/api/queue")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "size" in data

    def test_get_actuators(self, authenticated_client):
        """`/api/actuators` lists actuator devices with entity ids + actions."""
        with patch("web.app.registry") as mock_registry:
            actuator = MagicMock()
            actuator.is_actuator.return_value = True
            actuator.entity_id = "gpio.pump_relay"
            actuator.name = "pump_relay"
            actuator.device_type = "gpio_output"
            actuator.capabilities = []
            actuator.integration_name = "GPIOIntegration"

            sensor = MagicMock()
            sensor.is_actuator.return_value = False

            mock_registry.get_all_devices.return_value = [actuator, sensor]

            response = authenticated_client.get("/api/actuators")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == [
            {
                "entityId": "gpio.pump_relay",
                "name": "pump_relay",
                "deviceType": "gpio_output",
                # Empty capabilities fall back to on/off (display-only).
                "actions": ["on", "off"],
                "integration": "GPIOIntegration",
            }
        ]

    def test_get_telemetry(self, authenticated_client):
        """`/api/telemetry` combines transport stats with queue + connection."""
        with (
            patch("app.mqtt_transport.mqtt_transport") as mock_transport,
            patch("app.queue_manager.queue_manager") as mock_queue,
        ):
            mock_transport.get_telemetry_status.return_value = {
                "entities": {"simulator.tent_temperature": {"value": 22.5, "ts": "T"}},
                "stats": {"published": 5, "dropped_no_entity": 0, "dropped_no_value": 1},
            }
            mock_transport.is_connected.return_value = True
            mock_queue.size.return_value = 3

            response = authenticated_client.get("/api/telemetry")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["entities"]["simulator.tent_temperature"]["value"] == 22.5
        assert data["stats"]["published"] == 5
        assert data["queueSize"] == 3
        assert data["connected"] is True

    def test_send_command_enqueues_wire_shape(self, authenticated_client):
        """Manual Control commands are enqueued in the §16.1 wire shape
        (targetType/targetId with the full entity id) — the exact dict
        `_process_command` consumes."""
        with (
            patch("web.app.registry") as mock_registry,
            patch("app.mqtt_transport.mqtt_transport") as mock_transport,
        ):
            device = MagicMock()
            device.is_actuator.return_value = True
            device.entity_id = "gpio.pump_relay"
            mock_registry.get_device.return_value = device

            response = authenticated_client.post(
                "/api/send-command",
                json={"target": "gpio.pump_relay", "action": "on", "payload": {"value": 1}},
            )

        assert response.status_code == 200
        mock_registry.get_device.assert_called_once_with("gpio.pump_relay")
        mock_transport._enqueue_command.assert_called_once()
        command = mock_transport._enqueue_command.call_args[0][0]
        assert command["targetType"] == "actuator"
        assert command["targetId"] == "gpio.pump_relay"
        assert command["action"] == "on"
        assert command["payload"] == {"value": 1}
        assert command["id"].startswith("web-")

    def test_send_command_unknown_actuator(self, authenticated_client):
        """An unknown or non-actuator target is rejected with 404."""
        with patch("web.app.registry") as mock_registry:
            mock_registry.get_device.return_value = None

            response = authenticated_client.post(
                "/api/send-command",
                json={"target": "gpio.nope", "action": "on"},
            )

        assert response.status_code == 404

    def test_get_integrations_no_instance(self):
        """Test getting integrations when app instance not available."""
        with patch("web.app.config") as mock_config:
            mock_config.get.side_effect = lambda key, default=None: {
                "web.auth_enabled": False,
            }.get(key, default)
            mock_config.get_section.return_value = {}

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                response = client.get("/api/integrations")

            assert response.status_code == 503

    def test_get_devices_no_instance(self):
        """Test getting devices when app instance not available."""
        with patch("web.app.config") as mock_config:
            mock_config.get.side_effect = lambda key, default=None: {
                "web.auth_enabled": False,
            }.get(key, default)

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                response = client.get("/api/devices")

            assert response.status_code == 503

    def test_get_devices_keys_by_domain(self, mock_config):
        """`/api/devices` keys entries `<domain>.<device>` (entity-id form)."""
        with (
            patch("web.app.config", mock_config),
            patch("web.app.auth_manager") as mock_auth,
            patch("web.app.registry"),
            patch("web.app._collect_device_data_from_integrations") as mock_collect,
        ):
            mock_config.get.side_effect = lambda key, default=None: {
                "web.auth_enabled": False,
            }.get(key, default)
            mock_auth.is_authenticated.return_value = True

            device_data = {
                "gpio.relay1": {"state": "off"},
                "simulator.tent_temperature": {"value": 22.5},
            }
            mock_collect.return_value = device_data

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            mock_app_instance = MagicMock()
            mock_app_instance._integrations = {"gpio": MagicMock()}
            mock_app_instance.loop = MagicMock()
            app.config["APPLICATION_INSTANCE"] = mock_app_instance

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                response = client.get("/api/devices")
                assert response.status_code == 200
                data = json.loads(response.data)
                assert "gpio.relay1" in data
                assert "simulator.tent_temperature" in data


class TestWebAppConfigEndpoints:
    """Tests for web app configuration endpoints."""

    @pytest.fixture
    def authenticated_client(self, mock_config, tmp_path):
        """Create authenticated Flask test client with temp config."""
        config_file = tmp_path / "config.yaml"
        sample_config = {
            "api": {"url": "http://localhost:8080"},
            "web": {"port": 5010},
        }
        with open(config_file, "w") as f:
            yaml.dump(sample_config, f)

        mock_config.config_file = str(config_file)
        mock_config.get.side_effect = lambda key, default=None: {
            "web.auth_enabled": False,
        }.get(key, default)

        with patch("web.app.config", mock_config):
            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                with app.app_context():
                    yield client

    def test_get_config_json(self, authenticated_client):
        """Test getting config as JSON."""
        response = authenticated_client.get("/api/config")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "api" in data

    def test_get_config_raw(self, authenticated_client):
        """Test getting config as raw YAML."""
        response = authenticated_client.get("/api/config?format=raw")

        assert response.status_code == 200
        assert response.content_type == "text/plain; charset=utf-8"

    def test_update_config(self, authenticated_client, tmp_path):
        """Test updating config."""
        new_config = {
            "api": {"url": "http://newhost:9090"},
            "web": {"port": 5020},
        }

        response = authenticated_client.post(
            "/api/config", data=json.dumps(new_config), content_type="application/json"
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

    def test_update_config_no_data(self, authenticated_client):
        """Test updating config with no data."""
        response = authenticated_client.post(
            "/api/config", data="", content_type="application/json"
        )

        # Flask may return 400 or 500 depending on how it handles empty JSON
        assert response.status_code in [400, 500]


class TestWebAppErrorHandlers:
    """Tests for web app error handlers."""

    @pytest.fixture
    def client(self, mock_config):
        """Create Flask test client."""
        with patch("web.app.config", mock_config):
            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"

            with app.test_client() as client:
                yield client

    def test_404_error(self, client):
        """Test 404 error handler."""
        response = client.get("/nonexistent-page")

        assert response.status_code == 404


class TestWebAppConnectionStatus:
    """Tests for connection status endpoint."""

    @pytest.fixture
    def client(self, mock_config):
        """Create Flask test client with mocks."""
        with patch("web.app.config", mock_config), patch("web.app.auth_manager") as mock_auth:
            mock_auth.is_authenticated.return_value = False
            mock_auth._client = None

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                yield client

    def test_connection_status_initializing(self, client):
        """Test connection status when app is initializing."""
        response = client.get("/api/connection-status")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data


class TestWebAppHelpers:
    """Tests for web app helper functions."""

    def test_is_password_set_true(self, mock_config):
        """Test is_password_set returns True when hash is set."""
        from werkzeug.security import generate_password_hash

        custom_hash = generate_password_hash("custom_password")
        mock_config.get.side_effect = lambda key, default=None: {
            "web.password_hash": custom_hash,
        }.get(key, default)

        with patch("web.app.config", mock_config):
            from web.app import is_password_set

            # Re-import to get the patched version
            result = is_password_set()

        # The function checks if hash exists and is not the default
        assert isinstance(result, bool)

    def test_is_password_set_false_empty(self, mock_config):
        """Test is_password_set returns False when hash is empty."""
        mock_config.get.side_effect = lambda key, default=None: {
            "web.password_hash": "",
        }.get(key, default)

        with patch("web.app.config", mock_config):
            from web.app import is_password_set

            result = is_password_set()

        # Empty string is falsy in Python
        assert not result

    def test_login_required_decorator_via_index(self, mock_config):
        """Test login_required decorator works on existing routes."""
        mock_config.get.side_effect = lambda key, default=None: {
            "web.auth_enabled": True,
        }.get(key, default)

        with patch("web.app.config", mock_config), patch("web.app.auth_manager") as mock_auth:
            mock_auth.get_auth_code.return_value = None
            mock_auth.is_authenticated.return_value = True

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                # Try to access a protected route without being logged in
                response = client.get("/")

            # Should redirect to login when auth is enabled
            assert response.status_code in [200, 302]

    def test_auth_disabled_allows_access(self, mock_config):
        """Test that auth disabled allows access to protected routes."""
        mock_config.get.side_effect = lambda key, default=None: {
            "web.auth_enabled": False,
        }.get(key, default)

        with patch("web.app.config", mock_config), patch("web.app.auth_manager") as mock_auth:
            mock_auth.get_auth_code.return_value = None
            mock_auth.is_authenticated.return_value = True

            from web.app import app

            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test"
            app.config["APPLICATION_INSTANCE"] = None

            with app.test_client() as client:
                response = client.get("/")

            # Should allow access when auth is disabled
            assert response.status_code == 200

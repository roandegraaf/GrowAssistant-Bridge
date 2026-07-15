"""
Tests for the Integration base class and integration framework.

This module tests the Integration abstract base class, registration
decorator, config schema validation, and integration discovery.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from app.integrations import (
    ConfigurationError,
    Integration,
    _integration_by_config_key,
    _integration_classes,
    get_all_config_keys,
    get_all_integration_classes,
    get_integration_class,
    get_integration_class_by_config_key,
    register_integration,
)


class TestIntegrationBaseClass:
    """Tests for the Integration abstract base class."""

    @pytest.fixture
    def concrete_integration_class(self):
        """Create a concrete implementation of Integration."""

        class TestIntegration(Integration):
            """Concrete test integration."""

            async def connect(self) -> bool:
                return True

            async def send_data(self, data: dict[str, Any]) -> bool:
                return True

            async def receive_data(self) -> Generator[dict[str, Any], None, None]:
                yield {"test": "data"}

            async def get_device_data(self) -> dict[str, Any]:
                return {"device1": {"value": 42}}

        return TestIntegration

    def test_integration_initialization(self, concrete_integration_class):
        """Test basic integration initialization."""
        config = {"enabled": True, "setting": "value"}
        integration = concrete_integration_class(config)

        assert integration.config == config
        assert integration.name == "TestIntegration"

    def test_get_config_key_default(self, concrete_integration_class):
        """Test default config key derivation."""
        assert concrete_integration_class.get_config_key() == "test"

    def test_get_config_key_removes_integration_suffix(self):
        """Test config key removes 'Integration' suffix."""

        class MQTTIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        assert MQTTIntegration.get_config_key() == "mqtt"

    @pytest.mark.asyncio
    async def test_execute_command(self, concrete_integration_class):
        """Test execute_command delegates to send_data."""
        integration = concrete_integration_class({})

        result = await integration.execute_command("device1", "on", {"level": 100})

        assert result is True

    def test_register_capabilities_with_devices(self, concrete_integration_class):
        """Test register_capabilities with devices config."""
        config = {
            "devices": {
                "d1": {"name": "sensor1", "type": "temperature"},
                "d2": {"name": "pump1", "type": "pump"},
            }
        }
        integration = concrete_integration_class(config)

        mock_registry = MagicMock()
        integration.register_capabilities(mock_registry)

        mock_registry.register_integration_by_devices.assert_called_once()

    def test_register_capabilities_without_devices(self, concrete_integration_class):
        """Test register_capabilities without devices config."""
        integration = concrete_integration_class({})

        mock_registry = MagicMock()
        integration.register_capabilities(mock_registry)

        mock_registry.register_integration_by_devices.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_action_default(self, concrete_integration_class):
        """Test default handle_action returns False."""
        integration = concrete_integration_class({})

        result = await integration.handle_action({"id": "action-1"})

        assert result is False

    @pytest.mark.asyncio
    async def test_apply_settings_raises_not_implemented(self, concrete_integration_class):
        """Test apply_settings raises NotImplementedError by default."""
        integration = concrete_integration_class({})

        with pytest.raises(NotImplementedError):
            await integration.apply_settings({"setting": "value"})

    @pytest.mark.asyncio
    async def test_disconnect_default(self, concrete_integration_class):
        """Test default disconnect does nothing."""
        integration = concrete_integration_class({})

        # Should not raise
        await integration.disconnect()


class TestIntegrationConfigValidation:
    """Tests for integration config schema validation."""

    def test_config_schema_validation_success(self):
        """Test successful config validation."""

        class SampleConfig(BaseModel):
            host: str
            port: int
            enabled: bool = True

        class ValidatedIntegration(Integration):
            CONFIG_SCHEMA = SampleConfig

            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        config = {"host": "localhost", "port": 8080}
        integration = ValidatedIntegration(config)

        assert integration.validated_config is not None
        assert integration.validated_config.host == "localhost"
        assert integration.validated_config.port == 8080

    def test_config_schema_validation_failure(self):
        """Test config validation raises ConfigurationError."""

        class StrictConfig(BaseModel):
            required_field: str
            required_int: int

        class StrictIntegration(Integration):
            CONFIG_SCHEMA = StrictConfig

            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        with pytest.raises(ConfigurationError):
            StrictIntegration({})

    def test_validated_config_property_none_without_schema(self):
        """Test validated_config is None without schema."""

        class NoSchemaIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        integration = NoSchemaIntegration({})

        assert integration.validated_config is None


class TestIntegrationRegistration:
    """Tests for integration registration."""

    @pytest.fixture(autouse=True)
    def clear_registries(self):
        """Clear integration registries before and after each test."""
        original_classes = _integration_classes.copy()
        original_config_keys = _integration_by_config_key.copy()

        _integration_classes.clear()
        _integration_by_config_key.clear()

        yield

        _integration_classes.clear()
        _integration_classes.update(original_classes)
        _integration_by_config_key.clear()
        _integration_by_config_key.update(original_config_keys)

    def test_register_integration_decorator(self):
        """Test register_integration decorator."""

        @register_integration
        class CustomIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        assert "CustomIntegration" in _integration_classes
        assert "custom" in _integration_by_config_key
        assert _integration_classes["CustomIntegration"] is CustomIntegration

    def test_get_integration_class(self):
        """Test getting integration class by name."""

        @register_integration
        class FindableIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        result = get_integration_class("FindableIntegration")

        assert result is FindableIntegration

    def test_get_integration_class_not_found(self):
        """Test getting non-existent integration class."""
        result = get_integration_class("NonExistent")

        assert result is None

    def test_get_integration_class_by_config_key(self):
        """Test getting integration class by config key."""

        @register_integration
        class ConfigKeyIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        result = get_integration_class_by_config_key("configkey")

        assert result is ConfigKeyIntegration

    def test_get_integration_class_by_config_key_case_insensitive(self):
        """Test config key lookup is case insensitive."""

        @register_integration
        class CaseIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        result1 = get_integration_class_by_config_key("case")
        result2 = get_integration_class_by_config_key("CASE")

        assert result1 is CaseIntegration
        assert result2 is CaseIntegration

    def test_get_all_integration_classes(self):
        """Test getting all registered integration classes."""

        @register_integration
        class Integration1(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        @register_integration
        class Integration2(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        all_classes = get_all_integration_classes()

        assert len(all_classes) == 2
        assert "Integration1" in all_classes
        assert "Integration2" in all_classes

    def test_get_all_config_keys(self):
        """Test getting all config keys."""

        @register_integration
        class KeyIntegration(Integration):
            async def connect(self):
                pass

            async def send_data(self, data):
                pass

            async def receive_data(self):
                pass

            async def get_device_data(self):
                pass

        keys = get_all_config_keys()

        assert "key" in keys

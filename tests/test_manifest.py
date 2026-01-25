"""
Tests for Integration Manifest Module.

This module tests the IntegrationManifest dataclass and related enums
that provide metadata about integrations.
"""

import pytest
from pydantic import BaseModel

from app.integrations.manifest import (
    DeviceCategory,
    IntegrationManifest,
    IoTClass,
)


class TestDeviceCategoryEnum:
    """Tests for DeviceCategory enum."""

    def test_sensor_value(self):
        """Test SENSOR category value."""
        assert DeviceCategory.SENSOR.value == "sensor"

    def test_actuator_value(self):
        """Test ACTUATOR category value."""
        assert DeviceCategory.ACTUATOR.value == "actuator"

    def test_both_value(self):
        """Test BOTH category value."""
        assert DeviceCategory.BOTH.value == "both"

    def test_is_string_enum(self):
        """Test that DeviceCategory is a string enum."""
        assert isinstance(DeviceCategory.SENSOR, str)
        assert DeviceCategory.SENSOR == "sensor"


class TestIoTClassEnum:
    """Tests for IoTClass enum."""

    def test_local_polling_value(self):
        """Test LOCAL_POLLING IoT class value."""
        assert IoTClass.LOCAL_POLLING.value == "local_polling"

    def test_local_push_value(self):
        """Test LOCAL_PUSH IoT class value."""
        assert IoTClass.LOCAL_PUSH.value == "local_push"

    def test_cloud_polling_value(self):
        """Test CLOUD_POLLING IoT class value."""
        assert IoTClass.CLOUD_POLLING.value == "cloud_polling"

    def test_cloud_push_value(self):
        """Test CLOUD_PUSH IoT class value."""
        assert IoTClass.CLOUD_PUSH.value == "cloud_push"

    def test_is_string_enum(self):
        """Test that IoTClass is a string enum."""
        assert isinstance(IoTClass.LOCAL_POLLING, str)
        assert IoTClass.LOCAL_POLLING == "local_polling"


class TestIntegrationManifest:
    """Tests for IntegrationManifest dataclass."""

    def test_required_fields(self):
        """Test creating manifest with required fields only."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test Integration",
        )
        assert manifest.domain == "test"
        assert manifest.name == "Test Integration"

    def test_default_values(self):
        """Test default values are set correctly."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test Integration",
        )
        assert manifest.version == "1.0.0"
        assert manifest.description == ""
        assert manifest.documentation == ""
        assert manifest.requirements == []
        assert manifest.dependencies == []
        assert manifest.device_categories == [DeviceCategory.BOTH]
        assert manifest.config_schema is None
        assert manifest.iot_class == IoTClass.LOCAL_POLLING
        assert manifest.codeowners == []
        assert manifest.is_builtin is False

    def test_custom_values(self):
        """Test creating manifest with custom values."""
        manifest = IntegrationManifest(
            domain="mqtt",
            name="MQTT Integration",
            version="2.0.0",
            description="MQTT broker integration",
            documentation="https://docs.example.com/mqtt",
            requirements=["paho-mqtt>=2.0.0"],
            dependencies=["network"],
            device_categories=[DeviceCategory.SENSOR, DeviceCategory.ACTUATOR],
            iot_class=IoTClass.LOCAL_PUSH,
            codeowners=["@developer"],
            is_builtin=True,
        )
        assert manifest.domain == "mqtt"
        assert manifest.version == "2.0.0"
        assert manifest.description == "MQTT broker integration"
        assert manifest.requirements == ["paho-mqtt>=2.0.0"]
        assert manifest.iot_class == IoTClass.LOCAL_PUSH
        assert manifest.is_builtin is True

    def test_supports_sensors_with_sensor_category(self):
        """Test supports_sensors returns True for SENSOR category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.SENSOR],
        )
        assert manifest.supports_sensors() is True

    def test_supports_sensors_with_both_category(self):
        """Test supports_sensors returns True for BOTH category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.BOTH],
        )
        assert manifest.supports_sensors() is True

    def test_supports_sensors_false_with_actuator_only(self):
        """Test supports_sensors returns False for ACTUATOR only category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.ACTUATOR],
        )
        assert manifest.supports_sensors() is False

    def test_supports_actuators_with_actuator_category(self):
        """Test supports_actuators returns True for ACTUATOR category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.ACTUATOR],
        )
        assert manifest.supports_actuators() is True

    def test_supports_actuators_with_both_category(self):
        """Test supports_actuators returns True for BOTH category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.BOTH],
        )
        assert manifest.supports_actuators() is True

    def test_supports_actuators_false_with_sensor_only(self):
        """Test supports_actuators returns False for SENSOR only category."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.SENSOR],
        )
        assert manifest.supports_actuators() is False

    def test_supports_both_with_multiple_categories(self):
        """Test manifest with both SENSOR and ACTUATOR categories."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[DeviceCategory.SENSOR, DeviceCategory.ACTUATOR],
        )
        assert manifest.supports_sensors() is True
        assert manifest.supports_actuators() is True

    def test_to_dict(self):
        """Test converting manifest to dictionary."""
        manifest = IntegrationManifest(
            domain="mqtt",
            name="MQTT Integration",
            version="1.0.0",
            description="Test description",
            documentation="https://docs.example.com",
            requirements=["paho-mqtt>=2.0.0"],
            dependencies=["network"],
            device_categories=[DeviceCategory.SENSOR],
            iot_class=IoTClass.LOCAL_PUSH,
            codeowners=["@dev"],
            is_builtin=True,
        )

        result = manifest.to_dict()

        assert result["domain"] == "mqtt"
        assert result["name"] == "MQTT Integration"
        assert result["version"] == "1.0.0"
        assert result["description"] == "Test description"
        assert result["documentation"] == "https://docs.example.com"
        assert result["requirements"] == ["paho-mqtt>=2.0.0"]
        assert result["dependencies"] == ["network"]
        assert result["device_categories"] == ["sensor"]
        assert result["iot_class"] == "local_push"
        assert result["codeowners"] == ["@dev"]
        assert result["is_builtin"] is True

    def test_to_dict_default_values(self):
        """Test to_dict with default values."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
        )

        result = manifest.to_dict()

        assert result["device_categories"] == ["both"]
        assert result["iot_class"] == "local_polling"

    def test_from_dict(self):
        """Test creating manifest from dictionary."""
        data = {
            "domain": "mqtt",
            "name": "MQTT Integration",
            "version": "2.0.0",
            "description": "Test description",
            "documentation": "https://docs.example.com",
            "requirements": ["paho-mqtt>=2.0.0"],
            "dependencies": ["network"],
            "device_categories": ["sensor", "actuator"],
            "iot_class": "cloud_polling",
            "codeowners": ["@dev"],
            "is_builtin": True,
        }

        manifest = IntegrationManifest.from_dict(data)

        assert manifest.domain == "mqtt"
        assert manifest.name == "MQTT Integration"
        assert manifest.version == "2.0.0"
        assert manifest.description == "Test description"
        assert manifest.documentation == "https://docs.example.com"
        assert manifest.requirements == ["paho-mqtt>=2.0.0"]
        assert manifest.dependencies == ["network"]
        assert manifest.device_categories == [DeviceCategory.SENSOR, DeviceCategory.ACTUATOR]
        assert manifest.iot_class == IoTClass.CLOUD_POLLING
        assert manifest.codeowners == ["@dev"]
        assert manifest.is_builtin is True

    def test_from_dict_with_defaults(self):
        """Test from_dict uses defaults for missing fields."""
        data = {
            "domain": "test",
            "name": "Test Integration",
        }

        manifest = IntegrationManifest.from_dict(data)

        assert manifest.domain == "test"
        assert manifest.name == "Test Integration"
        assert manifest.version == "1.0.0"
        assert manifest.description == ""
        assert manifest.documentation == ""
        assert manifest.requirements == []
        assert manifest.dependencies == []
        assert manifest.device_categories == [DeviceCategory.BOTH]
        assert manifest.iot_class == IoTClass.LOCAL_POLLING
        assert manifest.codeowners == []
        assert manifest.is_builtin is False

    def test_from_dict_to_dict_roundtrip(self):
        """Test that from_dict and to_dict are inverse operations."""
        original = IntegrationManifest(
            domain="mqtt",
            name="MQTT",
            version="1.0.0",
            device_categories=[DeviceCategory.SENSOR],
            iot_class=IoTClass.LOCAL_PUSH,
        )

        serialized = original.to_dict()
        restored = IntegrationManifest.from_dict(serialized)

        assert restored.domain == original.domain
        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.device_categories == original.device_categories
        assert restored.iot_class == original.iot_class

    def test_config_schema_with_pydantic_model(self):
        """Test manifest with a Pydantic config schema."""

        class TestConfigSchema(BaseModel):
            port: int
            host: str

        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            config_schema=TestConfigSchema,
        )

        assert manifest.config_schema is TestConfigSchema

    def test_config_schema_not_in_to_dict(self):
        """Test that config_schema is not included in to_dict output."""

        class TestConfigSchema(BaseModel):
            port: int

        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            config_schema=TestConfigSchema,
        )

        result = manifest.to_dict()
        assert "config_schema" not in result

    def test_empty_device_categories_list(self):
        """Test manifest with empty device categories list."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            device_categories=[],
        )
        assert manifest.supports_sensors() is False
        assert manifest.supports_actuators() is False

    def test_multiple_requirements(self):
        """Test manifest with multiple requirements."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            requirements=["package1>=1.0.0", "package2>=2.0.0", "package3"],
        )
        assert len(manifest.requirements) == 3
        assert "package1>=1.0.0" in manifest.requirements

    def test_multiple_dependencies(self):
        """Test manifest with multiple dependencies."""
        manifest = IntegrationManifest(
            domain="test",
            name="Test",
            dependencies=["network", "gpio", "serial"],
        )
        assert len(manifest.dependencies) == 3
        assert "network" in manifest.dependencies

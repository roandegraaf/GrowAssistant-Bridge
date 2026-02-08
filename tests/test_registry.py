"""
Tests for the DeviceRegistry module.

This module tests device registration, querying, and the registry's
domain-based naming system.
"""

import pytest

from app.registry import DeviceCategory, DeviceInfo, DeviceRegistry


class TestDeviceCategory:
    """Tests for the DeviceCategory enum."""

    def test_sensor_value(self):
        """Test SENSOR enum value."""
        assert DeviceCategory.SENSOR.value == "sensor"

    def test_actuator_value(self):
        """Test ACTUATOR enum value."""
        assert DeviceCategory.ACTUATOR.value == "actuator"


class TestDeviceInfo:
    """Tests for the DeviceInfo dataclass."""

    def test_entity_id_property(self):
        """Test entity_id property format."""
        device = DeviceInfo(
            name="temp_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )

        assert device.entity_id == "mqtt.temp_sensor"

    def test_is_sensor(self):
        """Test is_sensor method."""
        sensor = DeviceInfo(
            name="temp",
            domain="test",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="TestIntegration",
        )

        assert sensor.is_sensor() is True
        assert sensor.is_actuator() is False

    def test_is_actuator(self):
        """Test is_actuator method."""
        actuator = DeviceInfo(
            name="pump",
            domain="test",
            device_type="pump",
            category=DeviceCategory.ACTUATOR,
            integration_name="TestIntegration",
        )

        assert actuator.is_actuator() is True
        assert actuator.is_sensor() is False

    def test_default_capabilities(self):
        """Test default capabilities list."""
        device = DeviceInfo(
            name="device",
            domain="test",
            device_type="sensor",
            category=DeviceCategory.SENSOR,
            integration_name="TestIntegration",
        )

        assert device.capabilities == []

    def test_default_metadata(self):
        """Test default metadata dict."""
        device = DeviceInfo(
            name="device",
            domain="test",
            device_type="sensor",
            category=DeviceCategory.SENSOR,
            integration_name="TestIntegration",
        )

        assert device.metadata == {}


class TestDeviceRegistry:
    """Tests for the DeviceRegistry class."""

    @pytest.fixture
    def registry(self):
        """Create a fresh DeviceRegistry instance."""
        from app.utils.singleton import SingletonMeta

        if DeviceRegistry in SingletonMeta._instances:
            del SingletonMeta._instances[DeviceRegistry]

        reg = DeviceRegistry()
        yield reg
        reg.clear()

    def test_register_device_creates_entity_id(self, registry):
        """Test that register_device returns entity_id."""
        entity_id = registry.register_device(
            name="temp_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )

        assert entity_id == "mqtt.temp_sensor"

    def test_register_device_stores_device(self, registry):
        """Test that register_device stores the device."""
        registry.register_device(
            name="temp_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )

        device = registry.get_device("mqtt.temp_sensor")
        assert device is not None
        assert device.name == "temp_sensor"
        assert device.domain == "mqtt"

    def test_register_sensor_convenience_method(self, registry):
        """Test register_sensor convenience method."""
        entity_id = registry.register_sensor(
            sensor_name="humidity",
            integration_name="MQTTIntegration",
        )

        assert entity_id == "mqtt.humidity"

        device = registry.get_device(entity_id)
        assert device.category == DeviceCategory.SENSOR

    def test_register_actuator_convenience_method(self, registry):
        """Test register_actuator convenience method."""
        entity_id = registry.register_actuator(
            actuator_name="pump1",
            integration_name="GPIOIntegration",
        )

        assert entity_id == "gpio.pump1"

        device = registry.get_device(entity_id)
        assert device.category == DeviceCategory.ACTUATOR

    def test_register_device_with_custom_capabilities(self, registry):
        """Test registering device with custom capabilities."""
        registry.register_device(
            name="smart_pump",
            domain="http",
            device_type="pump",
            category=DeviceCategory.ACTUATOR,
            integration_name="HTTPIntegration",
            capabilities=["on", "off", "speed", "schedule"],
        )

        device = registry.get_device("http.smart_pump")
        assert device.capabilities == ["on", "off", "speed", "schedule"]

    def test_register_device_with_metadata(self, registry):
        """Test registering device with metadata."""
        registry.register_device(
            name="temp_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
            metadata={"location": "grow_room", "model": "DHT22"},
        )

        device = registry.get_device("mqtt.temp_sensor")
        assert device.metadata["location"] == "grow_room"
        assert device.metadata["model"] == "DHT22"

    def test_register_device_overwrite_warning(self, registry):
        """Test that re-registering a device overwrites the old one."""
        registry.register_device(
            name="device1",
            domain="test",
            device_type="sensor",
            category=DeviceCategory.SENSOR,
            integration_name="Integration1",
        )

        # Re-register with different integration
        registry.register_device(
            name="device1",
            domain="test",
            device_type="sensor",
            category=DeviceCategory.SENSOR,
            integration_name="Integration2",
        )

        device = registry.get_device("test.device1")
        assert device.integration_name == "Integration2"

    def test_get_device_returns_none_for_unknown(self, registry):
        """Test that get_device returns None for unknown entity_id."""
        device = registry.get_device("unknown.device")
        assert device is None

    def test_find_device_by_name(self, registry):
        """Test finding device by name only."""
        registry.register_device(
            name="unique_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )

        device = registry.find_device("unique_sensor")
        assert device is not None
        assert device.name == "unique_sensor"

    def test_find_device_by_name_and_domain(self, registry):
        """Test finding device by name and domain."""
        registry.register_device(
            name="sensor1",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )
        registry.register_device(
            name="sensor1",
            domain="http",
            device_type="humidity",
            category=DeviceCategory.SENSOR,
            integration_name="HTTPIntegration",
        )

        mqtt_device = registry.find_device("sensor1", domain="mqtt")
        http_device = registry.find_device("sensor1", domain="http")

        assert mqtt_device.domain == "mqtt"
        assert http_device.domain == "http"

    def test_find_device_returns_none_for_unknown(self, registry):
        """Test that find_device returns None for unknown name."""
        device = registry.find_device("unknown_device")
        assert device is None

    def test_get_devices_by_domain(self, registry):
        """Test getting devices by domain."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_sensor("humidity", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        mqtt_devices = registry.get_devices_by_domain("mqtt")

        assert len(mqtt_devices) == 2
        assert all(d.domain == "mqtt" for d in mqtt_devices)

    def test_get_devices_by_type(self, registry):
        """Test getting devices by type."""
        registry.register_device(
            name="sensor1",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )
        registry.register_device(
            name="sensor2",
            domain="http",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="HTTPIntegration",
        )
        registry.register_device(
            name="sensor3",
            domain="gpio",
            device_type="humidity",
            category=DeviceCategory.SENSOR,
            integration_name="GPIOIntegration",
        )

        temp_devices = registry.get_devices_by_type("temperature")

        assert len(temp_devices) == 2
        assert all(d.device_type == "temperature" for d in temp_devices)

    def test_get_devices_by_integration(self, registry):
        """Test getting devices by integration."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_sensor("humidity", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        mqtt_devices = registry.get_devices_by_integration("MQTTIntegration")

        assert len(mqtt_devices) == 2
        assert all(d.integration_name == "MQTTIntegration" for d in mqtt_devices)

    def test_get_all_devices(self, registry):
        """Test getting all registered devices."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")
        registry.register_sensor("humidity", "HTTPIntegration")

        all_devices = registry.get_all_devices()

        assert len(all_devices) == 3

    def test_get_all_entity_ids(self, registry):
        """Test getting all entity IDs."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        entity_ids = registry.get_all_entity_ids()

        assert "mqtt.temp" in entity_ids
        assert "gpio.pump" in entity_ids
        assert len(entity_ids) == 2

    def test_get_sensor_integration(self, registry):
        """Test getting integration for a sensor."""
        registry.register_sensor("temp", "MQTTIntegration")

        integration = registry.get_sensor_integration("temp")
        assert integration == "MQTTIntegration"

    def test_get_actuator_integration(self, registry):
        """Test getting integration for an actuator."""
        registry.register_actuator("pump", "GPIOIntegration")

        integration = registry.get_actuator_integration("pump")
        assert integration == "GPIOIntegration"

    def test_get_all_sensors(self, registry):
        """Test getting all sensors."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_sensor("humidity", "HTTPIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        sensors = registry.get_all_sensors()

        assert len(sensors) == 2
        assert "temp" in sensors
        assert "humidity" in sensors

    def test_get_all_actuators(self, registry):
        """Test getting all actuators."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_actuator("pump1", "GPIOIntegration")
        registry.register_actuator("pump2", "GPIOIntegration")

        actuators = registry.get_all_actuators()

        assert len(actuators) == 2
        assert "pump1" in actuators
        assert "pump2" in actuators

    def test_get_device_types(self, registry):
        """Test getting all device types."""
        device_types = registry.get_device_types()

        # Should include predefined types
        assert "pump" in device_types
        assert "light" in device_types
        assert "temperature" in device_types

    def test_get_device_actions(self, registry):
        """Test getting actions for a device type."""
        actions = registry.get_device_actions("pump")
        assert actions == ["on", "off"]

        actions = registry.get_device_actions("fan")
        assert "speed" in actions

    def test_register_device_type_actions(self, registry):
        """Test registering custom actions for a device type."""
        registry.register_device_type_actions("custom_device", ["start", "stop", "pause"])

        actions = registry.get_device_actions("custom_device")
        assert actions == ["start", "stop", "pause"]

    def test_has_integration_for_action_valid(self, registry):
        """Test has_integration_for_action with valid action."""
        registry.register_actuator("pump1", "GPIOIntegration")

        has_integration = registry.has_integration_for_action("on_pump1")
        assert has_integration is True

    def test_has_integration_for_action_invalid(self, registry):
        """Test has_integration_for_action with invalid action."""
        has_integration = registry.has_integration_for_action("on_nonexistent")
        assert has_integration is False

    def test_has_integration_for_action_invalid_format(self, registry):
        """Test has_integration_for_action with invalid format."""
        has_integration = registry.has_integration_for_action("invalidformat")
        assert has_integration is False

    def test_register_integration_by_devices(self, registry):
        """Test registering integration by device config."""
        devices_config = {
            "device1": {"name": "temperature_sensor", "type": "temperature"},
            "device2": {"name": "water_pump", "type": "pump"},
            "device3": {"name": "humidity_sensor", "type": "humidity"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        assert registry.get_sensor_integration("temperature_sensor") == "TestIntegration"
        assert registry.get_actuator_integration("water_pump") == "TestIntegration"
        assert registry.get_sensor_integration("humidity_sensor") == "TestIntegration"

    def test_clear_removes_all_devices(self, registry):
        """Test that clear removes all devices."""
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        registry.clear()

        assert len(registry.get_all_devices()) == 0
        assert len(registry.get_all_sensors()) == 0
        assert len(registry.get_all_actuators()) == 0

    def test_derive_domain_from_integration_name(self, registry):
        """Test domain derivation from integration name."""
        assert registry._derive_domain("MQTTIntegration") == "mqtt"
        assert registry._derive_domain("GPIOIntegration") == "gpio"
        assert registry._derive_domain("HTTPIntegration") == "http"
        assert registry._derive_domain("CustomDevice") == "customdevice"


class TestDeviceRegistryEdgeCases:
    """Tests for edge cases and error handling in DeviceRegistry."""

    @pytest.fixture
    def registry(self):
        """Create a fresh DeviceRegistry instance."""
        from app.utils.singleton import SingletonMeta

        if DeviceRegistry in SingletonMeta._instances:
            del SingletonMeta._instances[DeviceRegistry]

        reg = DeviceRegistry()
        yield reg
        reg.clear()

    def test_register_integration_by_devices_invalid_dict(self, registry):
        """Test register_integration_by_devices with non-dict device config."""
        devices_config = {
            "device1": {"name": "temp_sensor", "type": "temperature"},
            "device2": "invalid_string",  # Invalid: should be dict
            "device3": ["invalid", "list"],  # Invalid: should be dict
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # Only device1 should be registered
        assert registry.get_sensor_integration("temp_sensor") == "TestIntegration"
        assert len(registry.get_all_devices()) == 1

    def test_register_integration_by_devices_missing_name(self, registry):
        """Test register_integration_by_devices with missing name field."""
        devices_config = {
            "device1": {"type": "temperature"},  # Missing 'name'
            "device2": {"name": "valid_sensor", "type": "humidity"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # Only device2 should be registered
        assert registry.get_sensor_integration("valid_sensor") == "TestIntegration"
        assert len(registry.get_all_devices()) == 1

    def test_register_integration_by_devices_missing_type(self, registry):
        """Test register_integration_by_devices with missing type field."""
        devices_config = {
            "device1": {"name": "sensor_without_type"},  # Missing 'type'
            "device2": {"name": "valid_pump", "type": "pump"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # Only device2 should be registered
        assert registry.get_actuator_integration("valid_pump") == "TestIntegration"
        assert len(registry.get_all_devices()) == 1

    def test_register_integration_by_devices_both_missing(self, registry):
        """Test register_integration_by_devices with both name and type missing."""
        devices_config = {
            "device1": {},  # Missing both 'name' and 'type'
            "device2": {"name": "valid_sensor", "type": "temperature"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # Only device2 should be registered
        assert registry.get_sensor_integration("valid_sensor") == "TestIntegration"
        assert len(registry.get_all_devices()) == 1

    def test_get_sensor_integration_not_found(self, registry, caplog):
        """Test get_sensor_integration logs warning when sensor not found."""
        import logging

        with caplog.at_level(logging.WARNING):
            integration = registry.get_sensor_integration("nonexistent_sensor")

        assert integration is None
        assert "No integration found for sensor 'nonexistent_sensor'" in caplog.text

    def test_get_actuator_integration_not_found(self, registry, caplog):
        """Test get_actuator_integration logs warning when actuator not found."""
        import logging

        with caplog.at_level(logging.WARNING):
            integration = registry.get_actuator_integration("nonexistent_actuator")

        assert integration is None
        assert "No integration found for actuator 'nonexistent_actuator'" in caplog.text

    def test_find_device_ambiguous_name(self, registry, caplog):
        """Test find_device logs warning for ambiguous device name."""
        import logging

        # Register multiple devices with same name in different domains
        registry.register_device(
            name="sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )
        registry.register_device(
            name="sensor",
            domain="http",
            device_type="humidity",
            category=DeviceCategory.SENSOR,
            integration_name="HTTPIntegration",
        )
        registry.register_device(
            name="sensor",
            domain="gpio",
            device_type="pressure",
            category=DeviceCategory.SENSOR,
            integration_name="GPIOIntegration",
        )

        with caplog.at_level(logging.WARNING):
            device = registry.find_device("sensor")

        # Should return first match but log warning
        assert device is not None
        assert device.name == "sensor"
        assert "Ambiguous device lookup for 'sensor'" in caplog.text
        assert "Use domain-qualified lookup" in caplog.text

    def test_find_device_single_match_no_warning(self, registry, caplog):
        """Test find_device with single match doesn't log warning."""
        import logging

        registry.register_device(
            name="unique_sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )

        with caplog.at_level(logging.WARNING):
            device = registry.find_device("unique_sensor")

        assert device is not None
        assert device.name == "unique_sensor"
        # Should not log ambiguity warning
        assert "Ambiguous device lookup" not in caplog.text

    def test_find_device_with_domain_no_ambiguity(self, registry, caplog):
        """Test find_device with domain specified avoids ambiguity."""
        import logging

        # Register multiple devices with same name in different domains
        registry.register_device(
            name="sensor",
            domain="mqtt",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="MQTTIntegration",
        )
        registry.register_device(
            name="sensor",
            domain="http",
            device_type="humidity",
            category=DeviceCategory.SENSOR,
            integration_name="HTTPIntegration",
        )

        with caplog.at_level(logging.WARNING):
            device = registry.find_device("sensor", domain="mqtt")

        assert device is not None
        assert device.domain == "mqtt"
        # Should not log ambiguity warning when domain is specified
        assert "Ambiguous device lookup" not in caplog.text

    def test_register_integration_by_devices_sensor_types(self, registry):
        """Test that sensor types are correctly categorized."""
        devices_config = {
            "temp": {"name": "temp_sensor", "type": "temperature"},
            "humid": {"name": "humid_sensor", "type": "humidity"},
            "water": {"name": "water_sensor", "type": "water_level"},
            "light": {"name": "light_sensor", "type": "light_sensor"},
            "ph": {"name": "ph_sensor", "type": "ph"},
            "ec": {"name": "ec_sensor", "type": "ec"},
            "pressure": {"name": "pressure_sensor", "type": "pressure"},
            "flow": {"name": "flow_sensor", "type": "flow"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # All should be registered as sensors
        assert registry.get_sensor_integration("temp_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("humid_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("water_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("light_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("ph_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("ec_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("pressure_sensor") == "TestIntegration"
        assert registry.get_sensor_integration("flow_sensor") == "TestIntegration"

        # None should be registered as actuators
        assert len(registry.get_all_actuators()) == 0

    def test_register_integration_by_devices_actuator_types(self, registry):
        """Test that non-sensor types are correctly categorized as actuators."""
        devices_config = {
            "pump": {"name": "water_pump", "type": "pump"},
            "light": {"name": "grow_light", "type": "light"},
            "fan": {"name": "ventilation_fan", "type": "fan"},
            "heater": {"name": "space_heater", "type": "heater"},
        }

        registry.register_integration_by_devices("TestIntegration", devices_config)

        # All should be registered as actuators
        assert registry.get_actuator_integration("water_pump") == "TestIntegration"
        assert registry.get_actuator_integration("grow_light") == "TestIntegration"
        assert registry.get_actuator_integration("ventilation_fan") == "TestIntegration"
        assert registry.get_actuator_integration("space_heater") == "TestIntegration"

        # None should be registered as sensors
        assert len(registry.get_all_sensors()) == 0

    def test_get_devices_by_domain_empty(self, registry):
        """Test getting devices by domain when domain doesn't exist."""
        devices = registry.get_devices_by_domain("nonexistent_domain")
        assert devices == []

    def test_get_devices_by_type_empty(self, registry):
        """Test getting devices by type when type doesn't exist."""
        devices = registry.get_devices_by_type("nonexistent_type")
        assert devices == []

    def test_get_devices_by_integration_empty(self, registry):
        """Test getting devices by integration when integration doesn't exist."""
        devices = registry.get_devices_by_integration("NonexistentIntegration")
        assert devices == []

    def test_get_device_actions_unknown_type(self, registry):
        """Test getting actions for unknown device type."""
        actions = registry.get_device_actions("unknown_device_type")
        assert actions == []

    def test_register_device_default_capabilities(self, registry):
        """Test that device gets default capabilities based on type."""
        registry.register_device(
            name="test_pump",
            domain="test",
            device_type="pump",
            category=DeviceCategory.ACTUATOR,
            integration_name="TestIntegration",
        )

        device = registry.get_device("test.test_pump")
        assert device.capabilities == ["on", "off"]

    def test_register_device_unknown_type_empty_capabilities(self, registry):
        """Test that unknown device type gets empty capabilities."""
        registry.register_device(
            name="custom_device",
            domain="test",
            device_type="unknown_custom_type",
            category=DeviceCategory.SENSOR,
            integration_name="TestIntegration",
        )

        device = registry.get_device("test.custom_device")
        assert device.capabilities == []

    def test_clear_resets_all_indexes(self, registry):
        """Test that clear resets all internal indexes."""
        # Register various devices
        registry.register_sensor("temp", "MQTTIntegration")
        registry.register_actuator("pump", "GPIOIntegration")

        # Verify devices exist
        assert len(registry.get_all_devices()) > 0

        # Clear registry
        registry.clear()

        # Verify all indexes are cleared
        assert len(registry._devices) == 0
        assert len(registry._sensors) == 0
        assert len(registry._actuators) == 0
        assert len(registry._by_domain) == 0
        assert len(registry._by_type) == 0
        assert len(registry._by_integration) == 0
        assert len(registry._by_category[DeviceCategory.SENSOR]) == 0
        assert len(registry._by_category[DeviceCategory.ACTUATOR]) == 0

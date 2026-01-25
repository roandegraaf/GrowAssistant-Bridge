"""
Tests for Configuration Schemas.

This module tests Pydantic model validation for integration configurations.
"""

import pytest
from pydantic import ValidationError

from app.schemas.config_schemas import (
    BaseIntegrationConfig,
    DeviceConfig,
    GenericIntegrationConfig,
    GPIOIntegrationConfig,
    GPIOPinConfig,
    HTTPEndpointConfig,
    HTTPIntegrationConfig,
    HTTPMethod,
    MQTTIntegrationConfig,
    MQTTTopicConfig,
    PinDirection,
    PinInitial,
    PullUpDown,
    SerialIntegrationConfig,
    SerialParity,
    validate_integration_config,
)


class TestBaseIntegrationConfig:
    """Tests for BaseIntegrationConfig."""

    def test_default_enabled_false(self):
        """Test that enabled defaults to False."""
        config = BaseIntegrationConfig()
        assert config.enabled is False

    def test_enabled_true(self):
        """Test setting enabled to True."""
        config = BaseIntegrationConfig(enabled=True)
        assert config.enabled is True

    def test_allows_extra_fields(self):
        """Test that extra fields are allowed."""
        config = BaseIntegrationConfig(enabled=True, custom_field="custom_value")
        assert config.enabled is True
        assert config.custom_field == "custom_value"


class TestPinDirectionEnum:
    """Tests for PinDirection enum."""

    def test_in_value(self):
        """Test IN direction value."""
        assert PinDirection.IN.value == "IN"

    def test_out_value(self):
        """Test OUT direction value."""
        assert PinDirection.OUT.value == "OUT"


class TestPinInitialEnum:
    """Tests for PinInitial enum."""

    def test_high_value(self):
        """Test HIGH initial value."""
        assert PinInitial.HIGH.value == "HIGH"

    def test_low_value(self):
        """Test LOW initial value."""
        assert PinInitial.LOW.value == "LOW"


class TestPullUpDownEnum:
    """Tests for PullUpDown enum."""

    def test_up_value(self):
        """Test UP pull value."""
        assert PullUpDown.UP.value == "UP"

    def test_down_value(self):
        """Test DOWN pull value."""
        assert PullUpDown.DOWN.value == "DOWN"


class TestGPIOPinConfig:
    """Tests for GPIOPinConfig."""

    def test_valid_config(self):
        """Test creating a valid GPIO pin configuration."""
        config = GPIOPinConfig(name="led1", pin=17, direction=PinDirection.OUT)
        assert config.name == "led1"
        assert config.pin == 17
        assert config.direction == PinDirection.OUT
        assert config.initial == PinInitial.LOW  # Default
        assert config.pull_up_down is None  # Default

    def test_pin_bounds_minimum(self):
        """Test pin number minimum bound (0)."""
        config = GPIOPinConfig(name="test", pin=0, direction=PinDirection.IN)
        assert config.pin == 0

    def test_pin_bounds_maximum(self):
        """Test pin number maximum bound (40)."""
        config = GPIOPinConfig(name="test", pin=40, direction=PinDirection.IN)
        assert config.pin == 40

    def test_pin_below_minimum_raises(self):
        """Test that pin below 0 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            GPIOPinConfig(name="test", pin=-1, direction=PinDirection.IN)
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_pin_above_maximum_raises(self):
        """Test that pin above 40 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            GPIOPinConfig(name="test", pin=41, direction=PinDirection.IN)
        assert "less than or equal to 40" in str(exc_info.value)

    def test_name_required(self):
        """Test that name is required."""
        with pytest.raises(ValidationError) as exc_info:
            GPIOPinConfig(pin=17, direction=PinDirection.OUT)
        assert "name" in str(exc_info.value)

    def test_empty_name_raises(self):
        """Test that empty name raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            GPIOPinConfig(name="", pin=17, direction=PinDirection.OUT)
        assert "at least 1 character" in str(exc_info.value).lower()

    def test_input_pin_with_pull_up(self):
        """Test input pin with pull-up resistor."""
        config = GPIOPinConfig(
            name="button",
            pin=18,
            direction=PinDirection.IN,
            pull_up_down=PullUpDown.UP,
        )
        assert config.direction == PinDirection.IN
        assert config.pull_up_down == PullUpDown.UP

    def test_output_pin_with_initial_high(self):
        """Test output pin with HIGH initial state."""
        config = GPIOPinConfig(
            name="relay",
            pin=23,
            direction=PinDirection.OUT,
            initial=PinInitial.HIGH,
        )
        assert config.direction == PinDirection.OUT
        assert config.initial == PinInitial.HIGH


class TestGPIOIntegrationConfig:
    """Tests for GPIOIntegrationConfig."""

    def test_default_pins_empty(self):
        """Test that pins defaults to empty dict."""
        config = GPIOIntegrationConfig()
        assert config.pins == {}
        assert config.enabled is False

    def test_with_pins(self):
        """Test configuration with pins."""
        pin_config = GPIOPinConfig(name="led1", pin=17, direction=PinDirection.OUT)
        config = GPIOIntegrationConfig(
            enabled=True,
            pins={"led1": pin_config},
        )
        assert config.enabled is True
        assert "led1" in config.pins
        assert config.pins["led1"].pin == 17


class TestMQTTTopicConfig:
    """Tests for MQTTTopicConfig."""

    def test_valid_config(self):
        """Test creating a valid MQTT topic configuration."""
        config = MQTTTopicConfig(name="sensors/temperature", type="temperature")
        assert config.name == "sensors/temperature"
        assert config.type == "temperature"

    def test_name_required(self):
        """Test that name is required."""
        with pytest.raises(ValidationError):
            MQTTTopicConfig(type="temperature")

    def test_type_required(self):
        """Test that type is required."""
        with pytest.raises(ValidationError):
            MQTTTopicConfig(name="sensors/temperature")

    def test_empty_name_raises(self):
        """Test that empty name raises validation error."""
        with pytest.raises(ValidationError):
            MQTTTopicConfig(name="", type="temperature")

    def test_empty_type_raises(self):
        """Test that empty type raises validation error."""
        with pytest.raises(ValidationError):
            MQTTTopicConfig(name="sensors/temperature", type="")


class TestMQTTIntegrationConfig:
    """Tests for MQTTIntegrationConfig."""

    def test_defaults(self):
        """Test default values."""
        config = MQTTIntegrationConfig()
        assert config.broker == "localhost"
        assert config.port == 1883
        assert config.username == ""
        assert config.password == ""
        assert config.client_id == "grow_assistant"
        assert config.topics == {}
        assert config.enabled is False

    def test_port_bounds_minimum(self):
        """Test port minimum bound (1)."""
        config = MQTTIntegrationConfig(port=1)
        assert config.port == 1

    def test_port_bounds_maximum(self):
        """Test port maximum bound (65535)."""
        config = MQTTIntegrationConfig(port=65535)
        assert config.port == 65535

    def test_port_below_minimum_raises(self):
        """Test that port below 1 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            MQTTIntegrationConfig(port=0)
        assert "greater than or equal to 1" in str(exc_info.value)

    def test_port_above_maximum_raises(self):
        """Test that port above 65535 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            MQTTIntegrationConfig(port=65536)
        assert "less than or equal to 65535" in str(exc_info.value)

    def test_with_auth(self):
        """Test configuration with authentication."""
        config = MQTTIntegrationConfig(
            broker="mqtt.example.com",
            port=8883,
            username="user",
            password="secret",
        )
        assert config.broker == "mqtt.example.com"
        assert config.port == 8883
        assert config.username == "user"
        assert config.password == "secret"

    def test_with_topics(self):
        """Test configuration with topics."""
        topic = MQTTTopicConfig(name="sensors/temp", type="temperature")
        config = MQTTIntegrationConfig(
            enabled=True,
            topics={"temp": topic},
        )
        assert "temp" in config.topics
        assert config.topics["temp"].name == "sensors/temp"


class TestHTTPMethodEnum:
    """Tests for HTTPMethod enum."""

    def test_get_value(self):
        """Test GET method value."""
        assert HTTPMethod.GET.value == "GET"

    def test_post_value(self):
        """Test POST method value."""
        assert HTTPMethod.POST.value == "POST"

    def test_put_value(self):
        """Test PUT method value."""
        assert HTTPMethod.PUT.value == "PUT"

    def test_patch_value(self):
        """Test PATCH method value."""
        assert HTTPMethod.PATCH.value == "PATCH"

    def test_delete_value(self):
        """Test DELETE method value."""
        assert HTTPMethod.DELETE.value == "DELETE"


class TestHTTPEndpointConfig:
    """Tests for HTTPEndpointConfig."""

    def test_valid_config(self):
        """Test creating a valid HTTP endpoint configuration."""
        config = HTTPEndpointConfig(name="weather", url="http://api.example.com/weather")
        assert config.name == "weather"
        assert config.url == "http://api.example.com/weather"

    def test_defaults(self):
        """Test default values."""
        config = HTTPEndpointConfig(name="test", url="http://example.com")
        assert config.method == HTTPMethod.GET
        assert config.headers == {}
        assert config.interval == 300

    def test_with_custom_method(self):
        """Test endpoint with custom HTTP method."""
        config = HTTPEndpointConfig(
            name="submit",
            url="http://api.example.com/data",
            method=HTTPMethod.POST,
        )
        assert config.method == HTTPMethod.POST

    def test_with_headers(self):
        """Test endpoint with custom headers."""
        config = HTTPEndpointConfig(
            name="api",
            url="http://api.example.com",
            headers={"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert config.headers["Authorization"] == "Bearer token"
        assert config.headers["Content-Type"] == "application/json"

    def test_interval_zero_disables_polling(self):
        """Test that interval 0 is valid (disables polling)."""
        config = HTTPEndpointConfig(name="test", url="http://example.com", interval=0)
        assert config.interval == 0

    def test_name_required(self):
        """Test that name is required."""
        with pytest.raises(ValidationError):
            HTTPEndpointConfig(url="http://example.com")

    def test_url_required(self):
        """Test that url is required."""
        with pytest.raises(ValidationError):
            HTTPEndpointConfig(name="test")


class TestHTTPIntegrationConfig:
    """Tests for HTTPIntegrationConfig."""

    def test_default_endpoints_empty(self):
        """Test that endpoints defaults to empty dict."""
        config = HTTPIntegrationConfig()
        assert config.endpoints == {}
        assert config.enabled is False

    def test_with_endpoints(self):
        """Test configuration with endpoints."""
        endpoint = HTTPEndpointConfig(name="api", url="http://api.example.com")
        config = HTTPIntegrationConfig(
            enabled=True,
            endpoints={"api": endpoint},
        )
        assert config.enabled is True
        assert "api" in config.endpoints
        assert config.endpoints["api"].url == "http://api.example.com"


class TestSerialParityEnum:
    """Tests for SerialParity enum."""

    def test_none_value(self):
        """Test NONE parity value."""
        assert SerialParity.NONE.value == "N"

    def test_even_value(self):
        """Test EVEN parity value."""
        assert SerialParity.EVEN.value == "E"

    def test_odd_value(self):
        """Test ODD parity value."""
        assert SerialParity.ODD.value == "O"

    def test_mark_value(self):
        """Test MARK parity value."""
        assert SerialParity.MARK.value == "M"

    def test_space_value(self):
        """Test SPACE parity value."""
        assert SerialParity.SPACE.value == "S"


class TestSerialIntegrationConfig:
    """Tests for SerialIntegrationConfig."""

    def test_defaults(self):
        """Test default values."""
        config = SerialIntegrationConfig()
        assert config.port is None
        assert config.baudrate == 9600
        assert config.bytesize == 8
        assert config.parity == SerialParity.NONE
        assert config.stopbits == 1
        assert config.timeout == 1.0
        assert config.devices == {}
        assert config.enabled is False

    def test_baudrate_bounds_minimum(self):
        """Test baudrate minimum bound (300)."""
        config = SerialIntegrationConfig(baudrate=300)
        assert config.baudrate == 300

    def test_baudrate_bounds_maximum(self):
        """Test baudrate maximum bound (4000000)."""
        config = SerialIntegrationConfig(baudrate=4000000)
        assert config.baudrate == 4000000

    def test_baudrate_below_minimum_raises(self):
        """Test that baudrate below 300 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            SerialIntegrationConfig(baudrate=299)
        assert "greater than or equal to 300" in str(exc_info.value)

    def test_baudrate_above_maximum_raises(self):
        """Test that baudrate above 4000000 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            SerialIntegrationConfig(baudrate=4000001)
        assert "less than or equal to 4000000" in str(exc_info.value)

    def test_bytesize_valid_values(self):
        """Test valid bytesize values."""
        for size in [5, 6, 7, 8]:
            config = SerialIntegrationConfig(bytesize=size)
            assert config.bytesize == size

    def test_bytesize_invalid_raises(self):
        """Test that invalid bytesize raises validation error."""
        with pytest.raises(ValidationError):
            SerialIntegrationConfig(bytesize=9)

    def test_stopbits_valid_values(self):
        """Test valid stopbits values."""
        for bits in [1, 1.5, 2]:
            config = SerialIntegrationConfig(stopbits=bits)
            assert config.stopbits == bits

    def test_stopbits_invalid_raises(self):
        """Test that invalid stopbits raises validation error."""
        with pytest.raises(ValidationError):
            SerialIntegrationConfig(stopbits=3)

    def test_full_config(self):
        """Test full serial configuration."""
        config = SerialIntegrationConfig(
            enabled=True,
            port="/dev/ttyUSB0",
            baudrate=115200,
            bytesize=8,
            parity=SerialParity.NONE,
            stopbits=1,
            timeout=2.0,
        )
        assert config.enabled is True
        assert config.port == "/dev/ttyUSB0"
        assert config.baudrate == 115200


class TestDeviceConfig:
    """Tests for DeviceConfig."""

    def test_valid_config(self):
        """Test creating a valid device configuration."""
        config = DeviceConfig(name="sensor1", type="temperature")
        assert config.name == "sensor1"
        assert config.type == "temperature"

    def test_allows_extra_fields(self):
        """Test that extra fields are allowed."""
        config = DeviceConfig(name="sensor1", type="temperature", custom_field="value")
        assert config.name == "sensor1"
        assert config.custom_field == "value"

    def test_name_required(self):
        """Test that name is required."""
        with pytest.raises(ValidationError):
            DeviceConfig(type="temperature")

    def test_type_required(self):
        """Test that type is required."""
        with pytest.raises(ValidationError):
            DeviceConfig(name="sensor1")

    def test_empty_name_raises(self):
        """Test that empty name raises validation error."""
        with pytest.raises(ValidationError):
            DeviceConfig(name="", type="temperature")

    def test_empty_type_raises(self):
        """Test that empty type raises validation error."""
        with pytest.raises(ValidationError):
            DeviceConfig(name="sensor1", type="")


class TestGenericIntegrationConfig:
    """Tests for GenericIntegrationConfig."""

    def test_defaults(self):
        """Test default values."""
        config = GenericIntegrationConfig()
        assert config.devices == {}
        assert config.update_interval == 60
        assert config.enabled is False

    def test_update_interval_minimum(self):
        """Test update_interval minimum bound (1)."""
        config = GenericIntegrationConfig(update_interval=1)
        assert config.update_interval == 1

    def test_update_interval_below_minimum_raises(self):
        """Test that update_interval below 1 raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            GenericIntegrationConfig(update_interval=0)
        assert "greater than or equal to 1" in str(exc_info.value)

    def test_with_devices(self):
        """Test configuration with devices."""
        device = DeviceConfig(name="sensor1", type="temperature")
        config = GenericIntegrationConfig(
            enabled=True,
            devices={"sensor1": device},
            update_interval=30,
        )
        assert config.enabled is True
        assert "sensor1" in config.devices
        assert config.update_interval == 30


class TestValidateIntegrationConfig:
    """Tests for validate_integration_config function."""

    def test_validates_against_schema(self):
        """Test successful validation against schema."""
        config_dict = {"enabled": True, "broker": "mqtt.local", "port": 1883}
        result = validate_integration_config(config_dict, MQTTIntegrationConfig)

        assert isinstance(result, MQTTIntegrationConfig)
        assert result.enabled is True
        assert result.broker == "mqtt.local"
        assert result.port == 1883

    def test_raises_validation_error(self):
        """Test that invalid config raises ValidationError."""
        config_dict = {"port": "invalid"}  # port should be int

        with pytest.raises(ValidationError):
            validate_integration_config(config_dict, MQTTIntegrationConfig)

    def test_applies_defaults(self):
        """Test that defaults are applied during validation."""
        config_dict = {"enabled": True}
        result = validate_integration_config(config_dict, MQTTIntegrationConfig)

        assert result.broker == "localhost"
        assert result.port == 1883
        assert result.topics == {}

    def test_validates_nested_structures(self):
        """Test validation of nested structures."""
        config_dict = {
            "enabled": True,
            "pins": {
                "led1": {"name": "led1", "pin": 17, "direction": "OUT"},
            },
        }
        result = validate_integration_config(config_dict, GPIOIntegrationConfig)

        assert isinstance(result, GPIOIntegrationConfig)
        assert "led1" in result.pins
        assert result.pins["led1"].pin == 17
        assert result.pins["led1"].direction == PinDirection.OUT

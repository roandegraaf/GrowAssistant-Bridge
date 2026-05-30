"""
Configuration Schemas for Integrations.

This module defines Pydantic models for validating integration configurations.
Each integration has its own schema that validates configuration before the
integration is instantiated.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BaseIntegrationConfig(BaseModel):
    """Base configuration schema for all integrations."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False


# =============================================================================
# GPIO Integration Schemas
# =============================================================================


class PinDirection(str, Enum):
    """GPIO pin direction."""

    IN = "IN"
    OUT = "OUT"


class PinInitial(str, Enum):
    """Initial state for output GPIO pins."""

    HIGH = "HIGH"
    LOW = "LOW"


class PullUpDown(str, Enum):
    """Pull-up/pull-down resistor configuration."""

    UP = "UP"
    DOWN = "DOWN"


class GPIOPinConfig(BaseModel):
    """Configuration for a single GPIO pin."""

    name: str = Field(..., min_length=1, description="Unique name for the pin")
    pin: int = Field(..., ge=0, le=40, description="BCM GPIO pin number (0-40)")
    direction: PinDirection = Field(..., description="Pin direction: IN or OUT")
    initial: PinInitial | None = Field(
        default=PinInitial.LOW, description="Initial state for output pins"
    )
    pull_up_down: PullUpDown | None = Field(
        default=None, description="Pull-up/pull-down resistor for input pins"
    )


class GPIOIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for GPIO integration."""

    pins: dict[str, GPIOPinConfig] = Field(
        default_factory=dict, description="Dictionary of pin configurations keyed by ID"
    )


# =============================================================================
# MQTT Integration Schemas
# =============================================================================


class MQTTTopicConfig(BaseModel):
    """Configuration for a single MQTT topic."""

    name: str = Field(..., min_length=1, description="MQTT topic name/path")
    type: str = Field(..., min_length=1, description="Device type (e.g., temperature, humidity)")


class MQTTIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for MQTT integration."""

    broker: str = Field(default="localhost", description="MQTT broker hostname")
    port: int = Field(default=1883, ge=1, le=65535, description="MQTT broker port")
    username: str = Field(default="", description="MQTT authentication username")
    password: str = Field(default="", description="MQTT authentication password")
    client_id: str = Field(default="grow_assistant", description="MQTT client identifier")
    topics: dict[str, MQTTTopicConfig] = Field(
        default_factory=dict, description="Dictionary of topic configurations keyed by ID"
    )


# =============================================================================
# HTTP Integration Schemas
# =============================================================================


class HTTPMethod(str, Enum):
    """HTTP request methods."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class HTTPEndpointConfig(BaseModel):
    """Configuration for a single HTTP endpoint."""

    name: str = Field(..., min_length=1, description="Unique name for the endpoint")
    url: str = Field(..., min_length=1, description="Endpoint URL")
    method: HTTPMethod = Field(default=HTTPMethod.GET, description="HTTP method")
    headers: dict[str, str] = Field(
        default_factory=dict, description="HTTP headers to include in requests"
    )
    interval: int = Field(
        default=300, ge=0, description="Polling interval in seconds (0 to disable polling)"
    )


class HTTPIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for HTTP integration."""

    endpoints: dict[str, HTTPEndpointConfig] = Field(
        default_factory=dict, description="Dictionary of endpoint configurations keyed by ID"
    )


# =============================================================================
# Serial Integration Schemas
# =============================================================================


class SerialParity(str, Enum):
    """Serial port parity settings."""

    NONE = "N"
    EVEN = "E"
    ODD = "O"
    MARK = "M"
    SPACE = "S"


class SerialIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for Serial integration."""

    port: str | None = Field(
        default=None, description="Serial port path (e.g., /dev/ttyUSB0, COM3)"
    )
    baudrate: int = Field(default=9600, ge=300, le=4000000, description="Baud rate")
    bytesize: Literal[5, 6, 7, 8] = Field(default=8, description="Number of data bits")
    parity: SerialParity = Field(default=SerialParity.NONE, description="Parity checking mode")
    stopbits: Literal[1, 1.5, 2] = Field(default=1, description="Number of stop bits")
    timeout: float = Field(default=1.0, ge=0, description="Read timeout in seconds")
    devices: dict[str, "DeviceConfig"] = Field(
        default_factory=dict, description="Dictionary of device configurations"
    )


# =============================================================================
# ESPHome Integration Schemas
# =============================================================================


class ESPHomeEntityConfig(BaseModel):
    """Mapping for a single ESPHome entity onto a GrowAssistant device.

    `key` matches the ESPHome entity's `object_id` (preferred) or `name`.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        ..., min_length=1, description="GrowAssistant device type (temperature, humidity, ...)"
    )
    name: str | None = Field(
        default=None, description="Override device name in the registry (defaults to entity key)"
    )
    log_type: str | None = Field(
        default=None,
        description="API LogType (TEMPERATURE, HUMIDITY, ...). If unset, derived from `type`.",
    )
    category: Literal["sensor", "actuator"] | None = Field(
        default=None, description="Force sensor/actuator category. Auto-detected if omitted."
    )


class ESPHomeDeviceConfig(BaseModel):
    """Configuration for a single ESPHome device (one ESP node)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, description="Friendly name for this ESPHome device")
    host: str = Field(..., min_length=1, description="Hostname or IP of the ESPHome device")
    port: int = Field(default=6053, ge=1, le=65535, description="Native API port (default 6053)")
    encryption_key: str | None = Field(
        default=None, description="Base64 noise PSK from the ESPHome `api: encryption: key:` block"
    )
    password: str | None = Field(
        default=None, description="Legacy password (only if not using encryption_key)"
    )
    entities: dict[str, ESPHomeEntityConfig] = Field(
        default_factory=dict,
        description=(
            "Optional explicit mapping of ESPHome entity object_id -> GrowAssistant device. "
            "If omitted, sensors are auto-mapped from device_class metadata."
        ),
    )


class ESPHomeIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for the native ESPHome integration."""

    devices: dict[str, ESPHomeDeviceConfig] = Field(
        default_factory=dict, description="Dictionary of ESPHome devices keyed by ID"
    )
    reconnect_interval: int = Field(
        default=10, ge=1, description="Seconds to wait between reconnect attempts"
    )


# =============================================================================
# Generic Device Configuration (for external integrations)
# =============================================================================


class DeviceConfig(BaseModel):
    """Generic device configuration used by external integrations."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, description="Device name")
    type: str = Field(..., min_length=1, description="Device type")


class GenericIntegrationConfig(BaseIntegrationConfig):
    """Generic configuration schema for external/custom integrations."""

    devices: dict[str, DeviceConfig] = Field(
        default_factory=dict, description="Dictionary of device configurations"
    )
    update_interval: int = Field(default=60, ge=1, description="Update interval in seconds")


# =============================================================================
# Camera Integration Schemas
# =============================================================================


class CameraConfig(BaseModel):
    """Configuration for a single camera stream.

    ``name`` becomes the entity_id ``camera.<name>`` and is used verbatim as
    the go2rtc stream key and ``?src=`` value. ``source`` is any go2rtc stream
    source string (e.g. ``ffmpeg:...``, ``rtsp://...``, ``exec:...``).
    """

    name: str = Field(..., min_length=1, description="Camera name; entity_id is camera.<name>")
    source: str = Field(..., min_length=1, description="go2rtc stream source string")


class CameraIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for the camera (go2rtc WebRTC broker) integration."""

    go2rtc_binary: str = Field(
        default="go2rtc", description="Path to, or name on PATH of, the go2rtc binary"
    )
    go2rtc_api_port: int = Field(
        default=1984, ge=1, le=65535, description="Port go2rtc's HTTP API listens on"
    )
    go2rtc_host: str = Field(
        default="127.0.0.1", description="Host go2rtc's HTTP API is reachable on"
    )
    cameras: list[CameraConfig] = Field(
        default_factory=list, description="List of cameras to expose as streams"
    )
    low_framerate_fps: float = Field(
        default=0.5,
        gt=0,
        le=30,
        description=(
            "Framerate of each camera's reduced-quality variant stream "
            "(camera.<name>_lofps), requested by the browser when its WebRTC "
            "path is TURN-relayed (adaptive framerate)."
        ),
    )
    stun_candidate_port: int = Field(
        default=8555,
        ge=1,
        le=65535,
        description=(
            "WebRTC port go2rtc advertises for STUN-based public-IP discovery "
            "(stun:<port> candidate). Lets a NAT'd go2rtc be reachable for the "
            "common remote case without a TURN relay of its own."
        ),
    )


# =============================================================================
# Validation Utilities
# =============================================================================


def validate_integration_config(config: dict[str, Any], schema: type[BaseModel]) -> BaseModel:
    """Validate integration configuration against a schema.

    Args:
        config: Configuration dictionary to validate.
        schema: Pydantic model class to validate against.

    Returns:
        Validated Pydantic model instance.

    Raises:
        pydantic.ValidationError: If validation fails.
    """
    return schema.model_validate(config)

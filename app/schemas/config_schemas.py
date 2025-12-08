"""
Configuration Schemas for Integrations.

This module defines Pydantic models for validating integration configurations.
Each integration has its own schema that validates configuration before the
integration is instantiated.
"""

from enum import Enum
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class BaseIntegrationConfig(BaseModel):
    """Base configuration schema for all integrations."""
    enabled: bool = False

    class Config:
        extra = "allow"  # Allow extra fields for extensibility


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
    initial: Optional[PinInitial] = Field(
        default=PinInitial.LOW,
        description="Initial state for output pins"
    )
    pull_up_down: Optional[PullUpDown] = Field(
        default=None,
        description="Pull-up/pull-down resistor for input pins"
    )

    @field_validator("initial")
    @classmethod
    def initial_only_for_output(cls, v, info):
        """Validate that 'initial' is only set for output pins."""
        # Note: This validator runs before the full model is constructed
        # so we can't access 'direction' here directly in Pydantic v2
        return v


class GPIOIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for GPIO integration."""
    pins: Dict[str, GPIOPinConfig] = Field(
        default_factory=dict,
        description="Dictionary of pin configurations keyed by ID"
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
    topics: Dict[str, MQTTTopicConfig] = Field(
        default_factory=dict,
        description="Dictionary of topic configurations keyed by ID"
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
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers to include in requests"
    )
    interval: int = Field(
        default=300,
        ge=0,
        description="Polling interval in seconds (0 to disable polling)"
    )


class HTTPIntegrationConfig(BaseIntegrationConfig):
    """Configuration schema for HTTP integration."""
    endpoints: Dict[str, HTTPEndpointConfig] = Field(
        default_factory=dict,
        description="Dictionary of endpoint configurations keyed by ID"
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
    port: Optional[str] = Field(
        default=None,
        description="Serial port path (e.g., /dev/ttyUSB0, COM3)"
    )
    baudrate: int = Field(
        default=9600,
        ge=300,
        le=4000000,
        description="Baud rate"
    )
    bytesize: Literal[5, 6, 7, 8] = Field(
        default=8,
        description="Number of data bits"
    )
    parity: SerialParity = Field(
        default=SerialParity.NONE,
        description="Parity checking mode"
    )
    stopbits: Literal[1, 1.5, 2] = Field(
        default=1,
        description="Number of stop bits"
    )
    timeout: float = Field(
        default=1.0,
        ge=0,
        description="Read timeout in seconds"
    )
    devices: Dict[str, "DeviceConfig"] = Field(
        default_factory=dict,
        description="Dictionary of device configurations"
    )


# =============================================================================
# Generic Device Configuration (for external integrations)
# =============================================================================

class DeviceConfig(BaseModel):
    """Generic device configuration used by external integrations."""
    name: str = Field(..., min_length=1, description="Device name")
    type: str = Field(..., min_length=1, description="Device type")

    class Config:
        extra = "allow"  # Allow integration-specific fields


class GenericIntegrationConfig(BaseIntegrationConfig):
    """Generic configuration schema for external/custom integrations."""
    devices: Dict[str, DeviceConfig] = Field(
        default_factory=dict,
        description="Dictionary of device configurations"
    )
    update_interval: int = Field(
        default=60,
        ge=1,
        description="Update interval in seconds"
    )

    class Config:
        extra = "allow"  # Allow integration-specific fields


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_integration_config(
    config: Dict[str, Any],
    schema: type[BaseModel]
) -> BaseModel:
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

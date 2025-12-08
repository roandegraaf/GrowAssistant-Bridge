"""
Integration Manifest Module.

This module defines the IntegrationManifest dataclass that provides metadata
about integrations, enabling self-description, validation, and discovery.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel


class DeviceCategory(str, Enum):
    """Category of device an integration can handle."""

    SENSOR = "sensor"
    ACTUATOR = "actuator"
    BOTH = "both"


class IoTClass(str, Enum):
    """Classification of how the integration communicates."""

    LOCAL_POLLING = "local_polling"  # Polls local device
    LOCAL_PUSH = "local_push"  # Local device pushes data
    CLOUD_POLLING = "cloud_polling"  # Polls cloud service
    CLOUD_PUSH = "cloud_push"  # Cloud service pushes data


@dataclass
class IntegrationManifest:
    """Metadata manifest for an integration.

    This class provides self-describing metadata about an integration,
    similar to Home Assistant's manifest.json pattern.

    Attributes:
        domain: Unique identifier for the integration (e.g., "mqtt", "gpio").
                This is used as the key in config.yaml.
        name: Human-readable name for display.
        version: Semantic version string (e.g., "1.0.0").
        description: Brief description of what the integration does.
        documentation: URL to documentation.
        requirements: List of pip package requirements (e.g., ["paho-mqtt>=2.0.0"]).
        dependencies: List of other integration domains this depends on.
        device_categories: What types of devices this integration handles.
        config_schema: Optional Pydantic model for config validation.
        iot_class: How the integration communicates with devices.
        codeowners: List of maintainer identifiers.
        is_builtin: Whether this is a built-in integration.
    """

    domain: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    documentation: str = ""
    requirements: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    device_categories: List[DeviceCategory] = field(default_factory=lambda: [DeviceCategory.BOTH])
    config_schema: Optional[Type[BaseModel]] = None
    iot_class: IoTClass = IoTClass.LOCAL_POLLING
    codeowners: List[str] = field(default_factory=list)
    is_builtin: bool = False

    def supports_sensors(self) -> bool:
        """Check if this integration supports sensors."""
        return (
            DeviceCategory.SENSOR in self.device_categories
            or DeviceCategory.BOTH in self.device_categories
        )

    def supports_actuators(self) -> bool:
        """Check if this integration supports actuators."""
        return (
            DeviceCategory.ACTUATOR in self.device_categories
            or DeviceCategory.BOTH in self.device_categories
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert manifest to dictionary for serialization."""
        return {
            "domain": self.domain,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "documentation": self.documentation,
            "requirements": self.requirements,
            "dependencies": self.dependencies,
            "device_categories": [dc.value for dc in self.device_categories],
            "iot_class": self.iot_class.value,
            "codeowners": self.codeowners,
            "is_builtin": self.is_builtin,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntegrationManifest":
        """Create manifest from dictionary."""
        device_categories = [DeviceCategory(dc) for dc in data.get("device_categories", ["both"])]
        iot_class = IoTClass(data.get("iot_class", "local_polling"))

        return cls(
            domain=data["domain"],
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            documentation=data.get("documentation", ""),
            requirements=data.get("requirements", []),
            dependencies=data.get("dependencies", []),
            device_categories=device_categories,
            iot_class=iot_class,
            codeowners=data.get("codeowners", []),
            is_builtin=data.get("is_builtin", False),
        )

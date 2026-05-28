"""Device Registry for managing sensors and actuators.

Uses domain-based naming system where entity IDs have format: domain.device_name
Example: mqtt.temperature, gpio.pump1
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class DeviceCategory(str, Enum):
    """Category of a device."""

    SENSOR = "sensor"
    ACTUATOR = "actuator"


@dataclass
class DeviceInfo:
    """Information about a registered device."""

    name: str
    domain: str
    device_type: str
    category: DeviceCategory
    integration_name: str
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_id(self) -> str:
        """Return entity ID in domain.name format."""
        return f"{self.domain}.{self.name}"

    def is_sensor(self) -> bool:
        return self.category == DeviceCategory.SENSOR

    def is_actuator(self) -> bool:
        return self.category == DeviceCategory.ACTUATOR


class DeviceRegistry(metaclass=SingletonMeta):
    """Registry of sensors and actuators using domain-qualified entity IDs.

    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the device registry."""
        self._devices: dict[str, DeviceInfo] = {}

        # Indexes for fast lookups
        self._by_domain: dict[str, set[str]] = {}
        self._by_type: dict[str, set[str]] = {}
        self._by_integration: dict[str, set[str]] = {}
        self._by_category: dict[DeviceCategory, set[str]] = {
            DeviceCategory.SENSOR: set(),
            DeviceCategory.ACTUATOR: set(),
        }

        # Legacy mappings for backward compatibility
        self._sensors: dict[str, str] = {}
        self._actuators: dict[str, str] = {}

        # Change-notification callbacks fired after register/remove.
        self._on_change_callbacks: list[Callable[[], None]] = []

        # Device type -> supported actions
        self._device_type_actions: dict[str, list[str]] = {
            "pump": ["on", "off"],
            "light": ["on", "off"],
            "fan": ["on", "off", "speed"],
            "heater": ["on", "off", "temperature"],
            "humidity": ["on", "off", "level"],
            "temperature": [],
            "water_level": [],
            "light_sensor": [],
            "ph": [],
            "ec": [],
            "pressure": [],
            "flow": [],
        }

        logger.info("Device Registry initialized")

    def register_device(
        self,
        name: str,
        domain: str,
        device_type: str,
        category: DeviceCategory,
        integration_name: str,
        capabilities: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Register a device with full domain qualification. Returns entity_id."""
        entity_id = f"{domain}.{name}"

        if entity_id in self._devices:
            existing = self._devices[entity_id]
            logger.warning(
                f"Device '{entity_id}' already registered by '{existing.integration_name}'. "
                f"Overwriting with '{integration_name}'."
            )
            self._remove_from_indexes(entity_id, existing)

        device_info = DeviceInfo(
            name=name,
            domain=domain,
            device_type=device_type,
            category=category,
            integration_name=integration_name,
            capabilities=capabilities or self._device_type_actions.get(device_type, []),
            metadata=metadata or {},
        )

        self._devices[entity_id] = device_info
        self._update_indexes(entity_id, device_info)

        # Update legacy mappings
        if category == DeviceCategory.SENSOR:
            self._sensors[name] = integration_name
        else:
            self._actuators[name] = integration_name

        logger.info(
            f"Registered {category.value} '{entity_id}' (type: {device_type}) "
            f"with '{integration_name}'"
        )

        self._fire_change_callbacks()
        return entity_id

    def _update_indexes(self, entity_id: str, device_info: DeviceInfo) -> None:
        """Update lookup indexes for a device."""
        self._by_domain.setdefault(device_info.domain, set()).add(entity_id)
        self._by_type.setdefault(device_info.device_type, set()).add(entity_id)
        self._by_integration.setdefault(device_info.integration_name, set()).add(entity_id)
        self._by_category[device_info.category].add(entity_id)

    def _remove_from_indexes(self, entity_id: str, device_info: DeviceInfo) -> None:
        """Remove device from all indexes."""
        for index, key in [
            (self._by_domain, device_info.domain),
            (self._by_type, device_info.device_type),
            (self._by_integration, device_info.integration_name),
        ]:
            if key in index:
                index[key].discard(entity_id)
        self._by_category[device_info.category].discard(entity_id)
        self._fire_change_callbacks()

    # ─── Change-callback machinery ──────────────────────────────────

    def add_change_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback fired whenever the registry's device set changes.

        Callbacks are invoked synchronously after every successful
        ``register_device`` and after ``_remove_from_indexes``. They MUST be
        cheap and non-blocking (typically: schedule an async task).
        """
        if cb not in self._on_change_callbacks:
            self._on_change_callbacks.append(cb)

    def remove_change_callback(self, cb: Callable[[], None]) -> None:
        """Deregister a previously-added change callback. No-op if absent."""
        try:
            self._on_change_callbacks.remove(cb)
        except ValueError:
            pass

    def _fire_change_callbacks(self) -> None:
        """Invoke every registered change callback. One bad callback never
        breaks the others — exceptions are logged and swallowed."""
        for cb in list(self._on_change_callbacks):
            try:
                cb()
            except Exception:
                logger.exception("Registry change callback raised")

    # ─── Manifest helpers ───────────────────────────────────────────

    def compute_manifest_hash(self) -> str:
        """SHA-256 hex digest over a deterministic serialization of the
        current device set.

        Stable under dict-ordering / set-ordering changes — two registries
        with the same logical contents always produce the same hash. Used by
        the heartbeat path to decide whether a manifest re-push is needed.
        """
        items: list[str] = []
        for entity_id in sorted(self._devices.keys()):
            d = self._devices[entity_id]
            payload = {
                "entityId": entity_id,
                "domain": d.domain,
                "name": d.name,
                "deviceType": d.device_type,
                # UPPERCASE to match the wire format used by serialize_manifest
                # and the API-side BridgeDeviceService.computeManifestHash.
                "category": d.category.value.upper(),
                "integrationName": d.integration_name,
                "capabilities": sorted(d.capabilities),
            }
            # Compact separators (no spaces) — must match the API-side hash
            # computation in BridgeDeviceService.computeManifestHash byte-for-byte.
            items.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
        return digest

    @staticmethod
    def _ha_entity_domain(d: DeviceInfo) -> str:
        """Map a device to its Home-Assistant-style entity domain.

        The app accepts exactly four values here:
          - SENSOR                                  → "sensor"
          - ACTUATOR, device_type "light"           → "light"
          - ACTUATOR with a "settable" capability   → "number"
            (capabilities include any of speed/level/temperature/set)
          - any other ACTUATOR                       → "switch"
        """
        if d.category == DeviceCategory.SENSOR:
            return "sensor"
        if d.device_type == "light":
            return "light"
        if any(cap in {"speed", "level", "temperature", "set"} for cap in d.capabilities):
            return "number"
        return "switch"

    def serialize_manifest(self, version: int) -> dict[str, Any]:
        """Build the JSON-serializable manifest payload for the API.

        ``version`` is the monotonic ``manifestVersion`` supplied by the
        caller (typically the transport, which persists it via
        ``config_store``). The registry deliberately does not read it
        directly to avoid a cyclic import / coupling.

        Each device carries the original 7 fields (unchanged, so the
        ``compute_manifest_hash`` parity check stays valid) plus three
        wire-only fields the app needs: ``entityDomain`` (HA entity
        domain), ``writable`` (True for actuators) and ``unit``.
        """
        devices = []
        for entity_id in sorted(self._devices.keys()):
            d = self._devices[entity_id]
            devices.append(
                {
                    "entityId": entity_id,
                    "domain": d.domain,
                    "name": d.name,
                    "deviceType": d.device_type,
                    "category": d.category.value.upper(),
                    "integrationName": d.integration_name,
                    "capabilities": list(d.capabilities),
                    "metadata": dict(d.metadata),
                    "entityDomain": self._ha_entity_domain(d),
                    "writable": d.category == DeviceCategory.ACTUATOR,
                    "unit": d.metadata.get("unit"),
                }
            )
        return {
            "manifestVersion": version,
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "devices": devices,
        }

    def _derive_domain(self, integration_name: str) -> str:
        """Derive domain from integration class name (e.g., GPIOIntegration -> gpio)."""
        name = integration_name
        if name.endswith("Integration"):
            name = name[:-11]
        return name.lower()

    def register_sensor(
        self,
        sensor_name: str,
        integration_name: str,
        domain: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> str:
        """Register a sensor. Convenience wrapper for register_device()."""
        return self.register_device(
            name=sensor_name,
            domain=domain or self._derive_domain(integration_name),
            device_type=device_type or sensor_name,
            category=DeviceCategory.SENSOR,
            integration_name=integration_name,
        )

    def register_actuator(
        self,
        actuator_name: str,
        integration_name: str,
        domain: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> str:
        """Register an actuator. Convenience wrapper for register_device()."""
        return self.register_device(
            name=actuator_name,
            domain=domain or self._derive_domain(integration_name),
            device_type=device_type or actuator_name,
            category=DeviceCategory.ACTUATOR,
            integration_name=integration_name,
        )

    def register_device_type_actions(self, device_type: str, actions: list[str]) -> None:
        """Register actions supported by a device type."""
        self._device_type_actions[device_type] = actions
        logger.info(f"Registered actions for '{device_type}': {actions}")

    def register_integration_by_devices(
        self, integration_name: str, devices_config: dict[str, Any]
    ) -> None:
        """Register devices from config, categorizing as sensor or actuator by type."""
        sensor_types = {
            "temperature",
            "humidity",
            "water_level",
            "light_sensor",
            "ph",
            "ec",
            "pressure",
            "flow",
        }

        for _device_id, device_config in devices_config.items():
            if not isinstance(device_config, dict):
                logger.error(f"Invalid device config for '{integration_name}': {device_config}")
                continue

            name = device_config.get("name")
            device_type = device_config.get("type")

            if not name or not device_type:
                logger.error(f"Invalid device config for '{integration_name}': {device_config}")
                continue

            if device_type in sensor_types:
                self.register_sensor(name, integration_name)
            else:
                self.register_actuator(name, integration_name)

    def get_sensor_integration(self, sensor_name: str) -> Optional[str]:
        """Get the integration that handles a sensor."""
        integration = self._sensors.get(sensor_name)
        if not integration:
            logger.warning(f"No integration found for sensor '{sensor_name}'")
        return integration

    def get_actuator_integration(self, actuator_name: str) -> Optional[str]:
        """Get the integration that handles an actuator."""
        integration = self._actuators.get(actuator_name)
        if not integration:
            logger.warning(f"No integration found for actuator '{actuator_name}'")
        return integration

    def get_all_sensors(self) -> dict[str, str]:
        """Get all registered sensors as name -> integration mapping."""
        return self._sensors.copy()

    def get_all_actuators(self) -> dict[str, str]:
        """Get all registered actuators as name -> integration mapping."""
        return self._actuators.copy()

    def get_device_types(self) -> list[str]:
        """Get all registered device types."""
        return list(self._device_type_actions.keys())

    def get_device_actions(self, device_type: str) -> list[str]:
        """Get available actions for a device type."""
        return self._device_type_actions.get(device_type, [])

    def has_integration_for_action(self, action_key: str) -> bool:
        """Check if there's an integration for an action key (format: action_target)."""
        try:
            _, target = action_key.split("_", 1)
            return target in self._actuators
        except ValueError:
            logger.warning(f"Invalid action key format: {action_key}")
            return False

    def get_device(self, entity_id: str) -> Optional[DeviceInfo]:
        """Get device by entity_id."""
        return self._devices.get(entity_id)

    def find_device(self, name: str, domain: Optional[str] = None) -> Optional[DeviceInfo]:
        """Find a device by name, optionally scoped to a domain."""
        if domain:
            return self._devices.get(f"{domain}.{name}")

        matches = [d for d in self._devices.values() if d.name == name]
        if len(matches) > 1:
            logger.warning(
                f"Ambiguous device lookup for '{name}': found in {[d.domain for d in matches]}. "
                f"Use domain-qualified lookup."
            )
        return matches[0] if matches else None

    def get_devices_by_domain(self, domain: str) -> list[DeviceInfo]:
        """Get all devices in a domain."""
        return [self._devices[eid] for eid in self._by_domain.get(domain, set())]

    def get_devices_by_type(self, device_type: str) -> list[DeviceInfo]:
        """Get all devices of a specific type."""
        return [self._devices[eid] for eid in self._by_type.get(device_type, set())]

    def get_devices_by_integration(self, integration_name: str) -> list[DeviceInfo]:
        """Get all devices managed by a specific integration."""
        return [self._devices[eid] for eid in self._by_integration.get(integration_name, set())]

    def get_all_devices(self) -> list[DeviceInfo]:
        """Get all registered devices."""
        return list(self._devices.values())

    def get_all_entity_ids(self) -> list[str]:
        """Get all registered entity IDs."""
        return list(self._devices.keys())

    def clear(self) -> None:
        """Clear the registry."""
        self._devices.clear()
        self._sensors.clear()
        self._actuators.clear()
        self._by_domain.clear()
        self._by_type.clear()
        self._by_integration.clear()
        self._by_category = {
            DeviceCategory.SENSOR: set(),
            DeviceCategory.ACTUATOR: set(),
        }
        logger.info("Registry cleared")


# Create a global instance for easy imports
registry = DeviceRegistry()

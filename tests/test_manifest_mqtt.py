"""Tests for the MQTT-era manifest serialization additions.

Covers the new ``entityDomain`` / ``writable`` / ``unit`` wire fields added to
``serialize_manifest`` and asserts that ``compute_manifest_hash`` is UNCHANGED
by that edit (the §5.2.1 fixture in docs/bridge-protocol.md must still produce
its verified digest).
"""

import pytest

from app.registry import DeviceCategory, DeviceRegistry
from app.utils.singleton import SingletonMeta

# The verified SHA-256 digest from docs/bridge-protocol.md §5.2.1.
FIXTURE_HASH = "f5b1954d657d7247d578bd15ff4e4bca827986bd88bc1c6a086886ac0ed158df"


@pytest.fixture
def reg():
    """Provide a fresh, isolated DeviceRegistry instance."""
    if DeviceRegistry in SingletonMeta._instances:
        del SingletonMeta._instances[DeviceRegistry]
    registry = DeviceRegistry()
    yield registry
    registry.clear()
    if DeviceRegistry in SingletonMeta._instances:
        del SingletonMeta._instances[DeviceRegistry]


def _device(devices: list[dict], entity_id: str) -> dict:
    """Find a single serialized device entry by entityId."""
    matches = [d for d in devices if d["entityId"] == entity_id]
    assert matches, f"{entity_id} not in manifest"
    return matches[0]


def test_compute_manifest_hash_unchanged(reg):
    """The §5.2.1 fixture must still produce the verified digest."""
    reg.register_device(
        name="pump1",
        domain="gpio",
        device_type="pump",
        category=DeviceCategory.ACTUATOR,
        integration_name="GPIOIntegration",
        capabilities=["on", "off"],
    )
    reg.register_device(
        name="temp1",
        domain="mqtt",
        device_type="temperature",
        category=DeviceCategory.SENSOR,
        integration_name="MQTTIntegration",
        capabilities=[],
    )
    assert reg.compute_manifest_hash() == FIXTURE_HASH


def test_serialize_sensor_fields(reg):
    """A sensor serializes entityDomain=sensor, writable=False, unit from metadata."""
    reg.register_device(
        name="tent1_temp",
        domain="sensor",
        device_type="temperature",
        category=DeviceCategory.SENSOR,
        integration_name="MQTTIntegration",
        capabilities=[],
        metadata={"unit": "°C"},
    )
    devices = reg.serialize_manifest(1)["devices"]
    entry = _device(devices, "sensor.tent1_temp")
    assert entry["entityDomain"] == "sensor"
    assert entry["writable"] is False
    assert entry["unit"] == "°C"


def test_serialize_switch_actuator(reg):
    """A plain on/off actuator serializes entityDomain=switch, writable=True, unit=None."""
    reg.register_device(
        name="pump1",
        domain="gpio",
        device_type="pump",
        category=DeviceCategory.ACTUATOR,
        integration_name="GPIOIntegration",
        capabilities=["on", "off"],
    )
    entry = _device(reg.serialize_manifest(1)["devices"], "gpio.pump1")
    assert entry["entityDomain"] == "switch"
    assert entry["writable"] is True
    assert entry["unit"] is None


def test_serialize_number_actuator(reg):
    """An actuator with a 'speed' capability serializes entityDomain=number."""
    reg.register_device(
        name="fan1",
        domain="gpio",
        device_type="fan",
        category=DeviceCategory.ACTUATOR,
        integration_name="GPIOIntegration",
        capabilities=["on", "off", "speed"],
    )
    entry = _device(reg.serialize_manifest(1)["devices"], "gpio.fan1")
    assert entry["entityDomain"] == "number"
    assert entry["writable"] is True


def test_serialize_light_actuator(reg):
    """An actuator of device_type 'light' serializes entityDomain=light."""
    reg.register_device(
        name="grow1",
        domain="gpio",
        device_type="light",
        category=DeviceCategory.ACTUATOR,
        integration_name="GPIOIntegration",
        capabilities=["on", "off"],
    )
    entry = _device(reg.serialize_manifest(1)["devices"], "gpio.grow1")
    assert entry["entityDomain"] == "light"
    assert entry["writable"] is True


def test_original_seven_fields_preserved(reg):
    """The original 7 manifest fields are still present and unchanged."""
    reg.register_device(
        name="pump1",
        domain="gpio",
        device_type="pump",
        category=DeviceCategory.ACTUATOR,
        integration_name="GPIOIntegration",
        capabilities=["on", "off"],
        metadata={"foo": "bar"},
    )
    entry = _device(reg.serialize_manifest(7)["devices"], "gpio.pump1")
    for key in (
        "entityId",
        "domain",
        "name",
        "deviceType",
        "category",
        "integrationName",
        "capabilities",
        "metadata",
    ):
        assert key in entry
    assert entry["category"] == "ACTUATOR"
    assert entry["metadata"] == {"foo": "bar"}

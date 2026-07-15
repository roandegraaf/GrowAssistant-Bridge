"""Parity tests for the shared entity-id derivation.

The manifest side (``registry.py``) and the telemetry side
(``mqtt_transport.py``) must build the ``<domain>.<name>`` join key the same
way, or telemetry stops joining to its manifest entity. Both now derive the
``domain`` half from :func:`app.entity_id.derive_domain`; these tests pin that
they agree.

Two levels of coverage:

* ``test_domain_derivation_parity`` isolates the *domain* half (holding the
  name constant on both sides) and asserts it is identical for every
  integration class — this is the half that used to be derived independently.
* ``test_end_to_end_join_*`` exercise each integration's *real* telemetry-point
  shape against its *real* registration, which is the honest end-to-end join.
  ``gpio``/``http`` join cleanly; ``esphome``/``mqtt`` have a *pre-existing*
  name divergence (the telemetry point does not carry the name the manifest
  registered), tracked as xfail — out of scope for the transport cleanup, see
  the task handoff.
"""

import pytest

from app.entity_id import derive_domain, derive_entity_id
from app.mqtt_transport import MqttTransport
from app.registry import registry

# Every integration class the bridge ships. The domain half of the join key is
# derived from these names identically on both sides.
INTEGRATION_CLASS_NAMES = [
    "GPIOIntegration",
    "HTTPIntegration",
    "MQTTIntegration",
    "SerialIntegration",
    "ESPHomeIntegration",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts from an empty singleton registry."""
    registry.clear()
    yield
    registry.clear()


@pytest.mark.parametrize("class_name", INTEGRATION_CLASS_NAMES)
def test_domain_derivation_parity(class_name):
    """Manifest and telemetry derive the same domain for a given class name.

    The name is held constant on both sides so this isolates the domain half —
    the piece that was previously duplicated between ``registry._derive_domain``
    and the inline strip in ``mqtt_transport._derive_entity_id``.
    """
    name = "widget"

    # Manifest side: register without an explicit domain so the registry's
    # derivation (now delegating to the shared helper) runs.
    manifest_entity_id = registry.register_sensor(name, class_name)

    # Telemetry side: the real derivation, falling back to the generic `name`
    # probe key so only the domain differs between integrations.
    telemetry_entity_id = MqttTransport._derive_entity_id({"integration": class_name, "name": name})

    assert manifest_entity_id == telemetry_entity_id
    assert manifest_entity_id == derive_entity_id(class_name, name)
    assert registry._derive_domain(class_name) == derive_domain(class_name)


def test_end_to_end_join_gpio():
    """A GPIO pin's telemetry point joins to its manifest entity."""
    manifest_entity_id = registry.register_sensor(
        "temperature_sensor", "GPIOIntegration", domain="gpio"
    )
    telemetry_entity_id = MqttTransport._derive_entity_id(
        {
            "integration": "GPIOIntegration",
            "pin_name": "temperature_sensor",
            "pin": 4,
            "state": "HIGH",
            "value": 1,
        }
    )
    assert manifest_entity_id == telemetry_entity_id == "gpio.temperature_sensor"


def test_end_to_end_join_http():
    """An HTTP endpoint's telemetry point joins to its manifest entity."""
    manifest_entity_id = registry.register_sensor("catfact", "HTTPIntegration", domain="http")
    telemetry_entity_id = MqttTransport._derive_entity_id(
        {
            "integration": "HTTPIntegration",
            "endpoint_name": "catfact",
            "url": "https://catfact.ninja/fact",
            "timestamp": 0,
        }
    )
    assert manifest_entity_id == telemetry_entity_id == "http.catfact"


@pytest.mark.xfail(
    reason="Pre-existing join divergence: the esphome telemetry point keys the "
    "name as '<device_id>:<object_id>' while the manifest registers "
    "'<device_name>_<object_id>'. Out of scope for transport cleanup — see handoff.",
    strict=True,
)
def test_end_to_end_join_esphome():
    """An ESPHome entity's telemetry point should join to its manifest entity."""
    # Manifest registers `<device_name>_<mapping_name>` (register_capabilities).
    manifest_entity_id = registry.register_sensor(
        "tent_sensors_temp", "ESPHomeIntegration", domain="esphome"
    )
    # Telemetry point as emitted by ESPHomeIntegration._handle_state.
    telemetry_entity_id = MqttTransport._derive_entity_id(
        {
            "integration": "ESPHomeIntegration",
            "device": "tent_sensors",
            "entity": "temp",
            "type": "temperature",
            "value": 23.6,
            "device_id": "tent_sensors:temp",
        }
    )
    assert manifest_entity_id == telemetry_entity_id


@pytest.mark.xfail(
    reason="Pre-existing join divergence: the mqtt telemetry point keys the name "
    "as the full 'topic' while the manifest registers the topic 'type'. Out of "
    "scope for transport cleanup — see handoff.",
    strict=True,
)
def test_end_to_end_join_mqtt():
    """An MQTT topic's telemetry point should join to its manifest entity."""
    # Manifest registers the topic *type* as the name (register_capabilities).
    manifest_entity_id = registry.register_sensor("temperature", "MQTTIntegration", domain="mqtt")
    # Telemetry point as emitted by MQTTIntegration._on_message.
    telemetry_entity_id = MqttTransport._derive_entity_id(
        {
            "integration": "MQTTIntegration",
            "topic": "sensors/temperature",
            "type": "temperature",
            "data": {"value": 23.6},
        }
    )
    assert manifest_entity_id == telemetry_entity_id

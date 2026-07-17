"""Telemetry-contract proof, per integration.

The contract: every sample an integration's ``receive_data`` yields carries an
explicit dotted ``entity_id`` equal to an entity id that integration's
``register_capabilities`` registered, plus a top-level ``value``. That is what
guarantees the sample joins its manifest entity on the app side — the exact
property that was broken for the mqtt/http/esphome/serial paths.

Each test registers the integration into a fresh real ``DeviceRegistry``,
drains ``receive_data``, and asserts the yielded ids are a subset of the
registered ids.
"""

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.integrations.esphome.esphome import ESPHomeIntegration
from app.integrations.http.http import HTTPIntegration
from app.integrations.mqtt.mqtt import MQTTIntegration
from app.integrations.serial.serial import SerialIntegration
from app.registry import DeviceRegistry


@pytest.fixture
def registry():
    """Provide a fresh (non-singleton-cached) DeviceRegistry."""
    from app.utils.singleton import SingletonMeta

    if DeviceRegistry in SingletonMeta._instances:
        del SingletonMeta._instances[DeviceRegistry]
    reg = DeviceRegistry()
    yield reg
    reg.clear()


def _assert_samples_join(samples: list[dict[str, Any]], registry: DeviceRegistry):
    """Every sample's entity_id must be a registered entity id."""
    registered = set(registry.get_all_entity_ids())
    assert samples, "integration yielded no samples"
    for sample in samples:
        assert "entity_id" in sample, f"sample missing entity_id: {sample}"
        assert sample["entity_id"] in registered, (
            f"sample entity_id {sample['entity_id']!r} does not join any "
            f"registered entity {sorted(registered)}"
        )
        assert "value" in sample, f"sample missing top-level value: {sample}"


class TestMQTTContract:
    """MQTT samples join `mqtt.<type>` regardless of the raw topic string."""

    def _integration(self):
        return MQTTIntegration(
            {
                "enabled": True,
                "topics": {
                    "0": {"name": "sensors/temperature", "type": "temperature"},
                    "1": {"name": "sensors/+/status", "type": "pump_status"},
                    "2": {
                        "name": "sensors/climate",
                        "type": "climate_json",
                        "value_key": "temp_c",
                    },
                },
            }
        )

    def _message(self, topic: str, payload: bytes):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload
        return msg

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        integration = self._integration()
        integration.register_capabilities(registry)
        integration._loop = asyncio.get_running_loop()
        integration.connected = True
        integration.client = MagicMock()

        # Scalar payload on an exact-match topic.
        integration._on_message(None, None, self._message("sensors/temperature", b"22.5"))
        # JSON payload on a wildcard-matched topic.
        integration._on_message(None, None, self._message("sensors/p1/status", b'{"value": "on"}'))
        # value_key extraction.
        integration._on_message(
            None, None, self._message("sensors/climate", b'{"temp_c": 21.7, "hum": 60}')
        )
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        by_id = {s["entity_id"]: s["value"] for s in samples}
        assert by_id == {
            "mqtt.temperature": 22.5,
            "mqtt.pump_status": "on",
            "mqtt.climate_json": 21.7,
        }

    @pytest.mark.asyncio
    async def test_unconfigured_topic_ignored(self, registry):
        integration = self._integration()
        integration.register_capabilities(registry)
        integration._loop = asyncio.get_running_loop()
        integration.connected = True
        integration.client = MagicMock()

        integration._on_message(None, None, self._message("some/other/topic", b"1"))
        await asyncio.sleep(0)

        samples = [s async for s in integration.receive_data()]
        assert samples == []


class TestHTTPContract:
    """HTTP samples join `http.<endpoint_name>` with the value extracted."""

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        integration = HTTPIntegration(
            {
                "enabled": True,
                "endpoints": {
                    "0": {"name": "api_temp", "url": "http://x/t"},
                    "1": {
                        "name": "api_nested",
                        "url": "http://x/n",
                        "value_key": "data.reading",
                    },
                },
            }
        )
        integration.register_capabilities(registry)

        now = time.time()
        integration.endpoints["api_temp"]["last_poll_result"] = {
            "timestamp": now,
            "status_code": 200,
            "data": {"value": 23.1},
        }
        integration.endpoints["api_nested"]["last_poll_result"] = {
            "timestamp": now,
            "status_code": 200,
            "data": {"data": {"reading": 55}},
        }

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        by_id = {s["entity_id"]: s["value"] for s in samples}
        assert by_id == {"http.api_temp": 23.1, "http.api_nested": 55}

    @pytest.mark.asyncio
    async def test_errored_poll_not_yielded(self, registry):
        integration = HTTPIntegration(
            {"enabled": True, "endpoints": {"0": {"name": "api_temp", "url": "http://x/t"}}}
        )
        integration.register_capabilities(registry)
        integration.endpoints["api_temp"]["last_poll_result"] = {
            "timestamp": time.time(),
            "error": "HTTP error: 500",
        }

        samples = [s async for s in integration.receive_data()]
        assert samples == []


class TestESPHomeContract:
    """ESPHome samples join `esphome.<device_name>_<mapping_name>`."""

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        integration = ESPHomeIntegration(
            {"enabled": True, "devices": {"tent": {"name": "tent", "host": "h"}}}
        )
        integration._loop = asyncio.get_running_loop()
        integration._runtime["tent"] = {
            "client": None,
            "task": None,
            "entities": {},
            "by_key": {
                7: {
                    "type": "temperature",
                    "category": "sensor",
                    "name": "dht_temp",
                    "object_id": "dht_temp",
                    "entity_name": "DHT Temp",
                    "esphome_kind": "SensorInfo",
                }
            },
            "connected": True,
            "name": "tent",
        }
        integration.register_capabilities(registry)

        state = MagicMock()
        state.key = 7
        state.missing_state = False
        state.state = 24.6
        integration._handle_state("tent", state)
        await asyncio.sleep(0)

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        assert samples[0]["entity_id"] == "esphome.tent_dht_temp"
        assert samples[0]["value"] == 24.6


class TestSerialContract:
    """Serial samples join `serial.<device>` for identified JSON lines."""

    def _integration(self):
        integration = SerialIntegration(
            {
                "enabled": True,
                "port": "/dev/null",
                "devices": {"0": {"name": "water_temp", "type": "temperature"}},
            }
        )
        # Simulate an open connection so receive_data drains the buffer.
        integration.serial_connected = True
        integration.serial = MagicMock()
        integration.serial.is_open = True
        return integration

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        integration = self._integration()
        integration.register_capabilities(registry)

        integration.read_buffer = [
            {"device": "water_temp", "value": 19.4, "timestamp": 0},
            {"entity_id": "serial.water_temp", "value": 19.5, "timestamp": 1},
        ]

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        assert [s["value"] for s in samples] == [19.4, 19.5]

    @pytest.mark.asyncio
    async def test_unidentified_lines_skipped(self, registry):
        integration = self._integration()
        integration.register_capabilities(registry)

        integration.read_buffer = [
            {"timestamp": 0, "data": "not json"},
            {"reading": 1, "timestamp": 0},
        ]

        samples = [s async for s in integration.receive_data()]
        assert samples == []


class TestGPIOContract:
    """GPIO samples join `gpio.<pin_name>` (mocked RPi.GPIO)."""

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry, monkeypatch):
        import app.integrations.gpio.gpio as gpio_module

        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0
        mock_gpio.input.return_value = 1
        monkeypatch.setattr(gpio_module, "GPIO_AVAILABLE", True)
        monkeypatch.setattr(gpio_module, "GPIO", mock_gpio, raising=False)

        integration = gpio_module.GPIOIntegration(
            {
                "enabled": True,
                "pins": {
                    "0": {"name": "float_switch", "pin": 4, "direction": "IN"},
                    "1": {"name": "pump_relay", "pin": 17, "direction": "OUT"},
                },
            }
        )
        integration.initialized = True
        integration.register_capabilities(registry)

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        assert samples[0]["entity_id"] == "gpio.float_switch"
        assert samples[0]["value"] == 1


class TestSimulatorContract:
    """Simulator samples join `simulator.<name>` (dev-default integration)."""

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        from external_integrations.simulator import SimulatorIntegration

        integration = SimulatorIntegration({"enabled": True})
        integration.register_capabilities(registry)

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        assert len(samples) == 5


class TestClimateContract:
    """Climate samples join `climate.<name>` (custom-domain registration)."""

    def _integration(self):
        from external_integrations.climate_control import ClimateControlIntegration

        return ClimateControlIntegration(
            {
                "enabled": True,
                "devices": {
                    "0": {"name": "tent_heater", "type": "heater"},
                    "1": {"name": "tent_humidifier", "type": "humidifier"},
                },
                "temperature_entity": "simulator.tent_temperature",
                "humidity_entity": "simulator.tent_humidity",
            }
        )

    @pytest.mark.asyncio
    async def test_samples_join_registered_entities(self, registry):
        integration = self._integration()
        integration.register_capabilities(registry)

        samples = [s async for s in integration.receive_data()]
        _assert_samples_join(samples, registry)
        assert {s["entity_id"] for s in samples} == {
            "climate.tent_heater",
            "climate.tent_humidifier",
        }

    @pytest.mark.asyncio
    async def test_on_telemetry_feeds_control_loop(self):
        """on_telemetry updates readings; the control logic then acts on them."""
        integration = self._integration()

        await integration.on_telemetry("simulator.tent_temperature", 18.0)
        await integration.on_telemetry("simulator.tent_humidity", "41.5")
        await integration.on_telemetry("other.entity", 99)
        await integration.on_telemetry("simulator.tent_temperature", "not-a-number")

        assert integration.current_temperature == 18.0
        assert integration.current_humidity == 41.5

    @pytest.mark.asyncio
    async def test_control_loop_acts_on_pushed_readings(self):
        """With a target above the pushed reading, one loop pass turns the
        heater on; when the reading overshoots, the next pass turns it off."""
        integration = self._integration()
        integration.update_interval = 0.01
        await integration.apply_settings({"climate": {"temperature": 24.0, "humidity": 60}})

        await integration.on_telemetry("simulator.tent_temperature", 20.0)
        task = asyncio.create_task(integration._control_loop())
        await asyncio.sleep(0.005)
        assert integration.heater_on is True

        await integration.on_telemetry("simulator.tent_temperature", 27.0)
        await asyncio.sleep(0.03)
        assert integration.heater_on is False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

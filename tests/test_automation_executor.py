"""Tests for the ActionExecutor — the HA-service → bridge-action seam.

The make-or-break trace (Phase-4 decision 9): a rule's ``call`` on entity
``switch.tent1_fan`` with service ``turn_on`` must reach
``integration.execute_command("tent1_fan", "on", payload)`` — device *name*,
translated action — not the entity id, not the HA service name.
"""

import pytest

from app.automations.executor import ActionExecutor, translate_service
from app.automations.state_store import StateStore
from app.registry import DeviceCategory, registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry.clear()
    yield
    registry.clear()


class FakeIntegration:
    def __init__(self):
        self.calls = []
        self.result = True

    async def execute_command(self, target_id, action, payload):
        self.calls.append((target_id, action, payload))
        return self.result


def _register(entity_id, category=DeviceCategory.ACTUATOR, integration_name="FakeIntegration"):
    domain, name = entity_id.split(".", 1)
    registry.register_device(
        name=name,
        domain=domain,
        device_type="fan",
        category=category,
        integration_name=integration_name,
    )


def _executor(integration, store=None):
    integrations = {"FakeIntegration": integration}
    return ActionExecutor(lambda n: integrations.get(n), state_store=store)


class TestServiceTranslation:
    def test_known_services_map_to_bridge_actions(self):
        assert translate_service("turn_on") == "on"
        assert translate_service("turn_off") == "off"
        assert translate_service("set_value") == "set"
        assert translate_service("set_percentage") == "speed"

    def test_unknown_service_passes_through_lowercased(self):
        # so a rule can target a device-specific action directly
        assert translate_service("Speed") == "speed"


class TestCall:
    async def test_traces_entity_and_service_to_execute_command(self):
        fake = FakeIntegration()
        _register("switch.tent1_fan")
        ok = await _executor(fake).call("switch.tent1_fan", "turn_on", {})
        assert ok is True
        # device NAME + translated action, not the entity id / HA service.
        assert fake.calls == [("tent1_fan", "on", {})]

    async def test_set_value_forwards_payload(self):
        fake = FakeIntegration()
        _register("number.tent1_target")
        await _executor(fake).call("number.tent1_target", "set_value", {"value": 22})
        assert fake.calls == [("tent1_target", "set", {"value": 22})]

    async def test_missing_entity_is_a_lazy_noop(self):
        fake = FakeIntegration()  # registry empty → entity unknown
        ok = await _executor(fake).call("switch.ghost", "turn_on", {})
        assert ok is False
        assert fake.calls == []

    async def test_missing_integration_is_a_noop(self):
        fake = FakeIntegration()
        _register("switch.x", integration_name="NotLoaded")
        ok = await _executor(fake).call("switch.x", "turn_on", {})
        assert ok is False
        assert fake.calls == []

    async def test_failed_command_does_not_write_back(self):
        fake = FakeIntegration()
        fake.result = False
        store = StateStore()
        _register("switch.fan")
        ok = await _executor(fake, store).call("switch.fan", "turn_on", {})
        assert ok is False
        assert store.get("switch.fan") is None


class TestWriteBack:
    async def test_on_off_write_back_to_store(self):
        fake = FakeIntegration()
        store = StateStore()
        _register("switch.fan")
        ex = _executor(fake, store)
        await ex.call("switch.fan", "turn_on", {})
        assert store.get("switch.fan") == 1
        await ex.call("switch.fan", "turn_off", {})
        assert store.get("switch.fan") == 0

    async def test_set_writes_back_the_payload_value(self):
        fake = FakeIntegration()
        store = StateStore()
        _register("number.target")
        await _executor(fake, store).call("number.target", "set_value", {"value": 19})
        assert store.get("number.target") == 19

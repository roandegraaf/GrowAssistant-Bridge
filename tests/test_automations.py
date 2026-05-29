"""Tests for the bridge automation manager (Phase 4 push path).

Covers: structural + entity-existence validation across the full vocabulary,
the empty-payload clear, the hash echo, and — the load-bearing case — that a
rule set received before its entities are registered fails validation on
receipt and then self-corrects when the registry changes.
"""

import hashlib
import json

import pytest

from app.automations import AutomationManager
from app.registry import DeviceCategory, registry


@pytest.fixture(autouse=True)
def clean_registry():
    """Start each test with an empty device registry (it is a module global)."""
    registry.clear()
    yield
    registry.clear()


def _register(entity_id: str, category=DeviceCategory.SENSOR):
    domain, name = entity_id.split(".", 1)
    registry.register_device(
        name=name,
        domain=domain,
        device_type="temperature",
        category=category,
        integration_name="TestIntegration",
    )


def _payload(automations) -> bytes:
    return json.dumps({"automations": automations}).encode("utf-8")


FULL_RULE = {
    "id": "a1",
    "name": "Vent",
    "enabled": True,
    "triggers": [
        {"type": "numeric_state", "entity": "sensor.temp", "above": 30},
        {"type": "time", "at": "06:30"},
        {"type": "time_pattern", "minutes": "/5"},
        {"type": "event", "event_type": "rule_set_applied"},
    ],
    "conditions": [
        {
            "type": "and",
            "conditions": [
                {"type": "state", "entity": "switch.fan", "state": "off"},
                {"type": "or", "conditions": [{"type": "time", "after": "06:00"}]},
            ],
        }
    ],
    "actions": [
        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
        {"type": "delay", "seconds": 600},
        {"type": "set_variable", "name": "x", "value": 1},
        {"type": "fire_event", "event_type": "cooldown"},
    ],
}


class TestValidate:
    def test_full_vocabulary_validates_against_a_populated_registry(self):
        _register("sensor.temp")
        _register("switch.fan", DeviceCategory.ACTUATOR)
        mgr = AutomationManager()
        assert mgr.validate([FULL_RULE]) == []

    def test_unknown_entity_is_reported(self):
        _register("switch.fan", DeviceCategory.ACTUATOR)  # sensor.temp missing
        mgr = AutomationManager()
        errors = mgr.validate([FULL_RULE])
        msgs = [e["message"] for e in errors]
        assert any("unknown entity 'sensor.temp'" in m for m in msgs)
        assert all(e["automationId"] == "a1" for e in errors)

    def test_unknown_trigger_and_action_types(self):
        mgr = AutomationManager()
        errors = mgr.validate(
            [
                {
                    "id": "a1",
                    "triggers": [{"type": "sunrise"}],
                    "actions": [{"type": "explode"}],
                }
            ]
        )
        msgs = " ".join(e["message"] for e in errors)
        assert "unknown trigger type 'sunrise'" in msgs
        assert "unknown action type 'explode'" in msgs

    def test_missing_triggers_or_actions(self):
        mgr = AutomationManager()
        errors = mgr.validate([{"id": "a1", "triggers": [], "actions": []}])
        msgs = " ".join(e["message"] for e in errors)
        assert "at least one trigger" in msgs
        assert "at least one action" in msgs

    def test_nested_condition_entity_is_validated(self):
        _register("sensor.temp")
        _register("switch.fan", DeviceCategory.ACTUATOR)
        mgr = AutomationManager()
        rule = {
            "id": "a1",
            "triggers": [{"type": "time", "at": "06:00"}],
            "conditions": [
                {
                    "type": "not",
                    "conditions": [{"type": "state", "entity": "sensor.ghost", "state": "x"}],
                }
            ],
            "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
        }
        errors = mgr.validate([rule])
        assert any("unknown entity 'sensor.ghost'" in e["message"] for e in errors)


class TestApplyPayload:
    async def test_valid_payload_publishes_ok_status_with_hash_echo(self):
        _register("sensor.temp")
        _register("switch.fan", DeviceCategory.ACTUATOR)
        published = []
        mgr = AutomationManager()
        mgr.set_status_publisher(lambda s: published.append(s) or _noop())

        raw = _payload([FULL_RULE])
        status = await mgr.apply_payload(raw)

        assert status["ok"] is True
        assert status["count"] == 1
        assert status["errors"] == []
        # validatedHash echoes the SHA-256 of the exact received bytes (what the
        # app stores as automationsPublishedHash for the same retained message).
        assert status["validatedHash"] == hashlib.sha256(raw).hexdigest()
        assert published and published[0] is status

    async def test_empty_payload_clears_and_reports_zero_count(self):
        published = []
        mgr = AutomationManager()
        mgr.set_status_publisher(lambda s: published.append(s) or _noop())

        status = await mgr.apply_payload(b"")
        assert status["ok"] is True
        assert status["count"] == 0
        # sha256 of empty bytes — matches the app's hashPayload("") on clear-of-last.
        assert status["validatedHash"] == hashlib.sha256(b"").hexdigest()

    async def test_invalid_json_reports_not_ok(self):
        mgr = AutomationManager()
        status = await mgr.apply_payload(b"{ not json")
        assert status["ok"] is False
        assert status["errors"][0]["message"].startswith("invalid payload")

    async def test_unknown_entity_reports_error_status(self):
        mgr = AutomationManager()  # empty registry
        status = await mgr.apply_payload(_payload([FULL_RULE]))
        assert status["ok"] is False
        assert any("unknown entity" in e["message"] for e in status["errors"])


class TestRevalidateOnRegistryChange:
    async def test_retained_before_integrations_then_self_corrects(self):
        """The rule set arrives before devices register → error; once the
        registry is populated, revalidate() reports ok. This is the
        retained-before-integrations-ready case."""
        published = []
        mgr = AutomationManager()
        mgr.set_status_publisher(lambda s: published.append(s) or _noop())

        # Received on an empty registry → unknown entities.
        first = await mgr.apply_payload(_payload([FULL_RULE]))
        assert first["ok"] is False

        # Integrations now register their devices.
        _register("sensor.temp")
        _register("switch.fan", DeviceCategory.ACTUATOR)

        second = await mgr.revalidate()
        assert second is not None
        assert second["ok"] is True
        # Same bytes → identical validatedHash across the receipt and the re-check.
        assert second["validatedHash"] == first["validatedHash"]

    async def test_revalidate_is_noop_before_any_payload(self):
        mgr = AutomationManager()
        assert await mgr.revalidate() is None


async def _noop():
    return None

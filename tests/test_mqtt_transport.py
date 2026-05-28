"""Tests for the MqttTransport singleton.

Covers topic construction, the telemetry payload shape, command-ack publishing,
and the on_message filtering rules (cmd vs own /ack echo vs automations). paho's
Client is mocked — no live broker is required.
"""

import json
from unittest.mock import MagicMock

import pytest

from app.mqtt_transport import MqttTransport
from app.utils.singleton import SingletonMeta


@pytest.fixture
def transport(monkeypatch):
    """Provide a fresh MqttTransport wired to a mocked, 'connected' paho client.

    The auth_manager getters are patched so topic construction is deterministic
    without real credentials.
    """
    if MqttTransport in SingletonMeta._instances:
        del SingletonMeta._instances[MqttTransport]

    import app.mqtt_transport as mod

    monkeypatch.setattr(mod.auth_manager, "get_tenant_id", lambda: "tenantX")
    monkeypatch.setattr(mod.auth_manager, "get_client_id", lambda: "bridgeY")
    monkeypatch.setattr(mod.auth_manager, "is_authenticated", lambda: True)

    t = MqttTransport()
    t._client = MagicMock()
    t._connected = True
    yield t

    if MqttTransport in SingletonMeta._instances:
        del SingletonMeta._instances[MqttTransport]


def test_topic_prefix_and_topics(transport):
    """Topics are tenant + bridge scoped, exactly per the contract."""
    assert transport._topic_prefix() == "ga/tenantX/bridge/bridgeY/"
    assert transport._topic("manifest") == "ga/tenantX/bridge/bridgeY/manifest"
    assert transport._topic("state") == "ga/tenantX/bridge/bridgeY/state"
    assert transport._topic("telemetry") == "ga/tenantX/bridge/bridgeY/telemetry"
    assert transport._topic("cmd/+") == "ga/tenantX/bridge/bridgeY/cmd/+"
    assert transport._topic("automations") == "ga/tenantX/bridge/bridgeY/automations"


@pytest.mark.asyncio
async def test_send_data_telemetry_payload(transport):
    """send_data builds {"samples":[...]} with entityId/value/ts and publishes
    to the telemetry topic at qos1, not retained."""
    points = [
        {"integration": "GPIOIntegration", "pin_name": "pump1", "value": 1, "timestamp": 0},
        {"integration": "MQTTIntegration", "topic": "temp1", "value": "22.5", "timestamp": 1000},
        # No derivable entity_id → dropped.
        {"value": 99, "timestamp": 2000},
    ]
    ok, _ = await transport.send_data(points)
    assert ok is True

    transport._client.publish.assert_called_once()
    args, kwargs = transport._client.publish.call_args
    topic = args[0]
    payload = json.loads(args[1])

    assert topic == "ga/tenantX/bridge/bridgeY/telemetry"
    assert kwargs["qos"] == 1
    assert kwargs["retain"] is False

    samples = payload["samples"]
    assert len(samples) == 2  # third point dropped
    assert samples[0] == {
        "entityId": "gpio.pump1",
        "value": 1,
        "ts": "1970-01-01T00:00:00Z",
    }
    assert samples[1]["entityId"] == "mqtt.temp1"
    assert samples[1]["value"] == "22.5"  # value passed through uncoerced
    assert samples[1]["ts"] == "1970-01-01T00:00:01Z"


@pytest.mark.asyncio
async def test_send_command_result_ack_topic(transport):
    """send_command_result publishes to cmd/{id}/ack with the right payload."""
    ok = await transport.send_command_result("cmd-123", True, "done")
    assert ok is True

    args, kwargs = transport._client.publish.call_args
    assert args[0] == "ga/tenantX/bridge/bridgeY/cmd/cmd-123/ack"
    assert kwargs["qos"] == 1
    assert kwargs["retain"] is False

    payload = json.loads(args[1])
    assert payload["id"] == "cmd-123"
    assert payload["success"] is True
    assert payload["message"] == "done"
    assert isinstance(payload["ts"], int)


def test_on_message_enqueues_command(transport):
    """A cmd/{id} message is enqueued for processing."""
    enqueued = []
    transport._enqueue_command = enqueued.append

    msg = MagicMock()
    msg.topic = "ga/tenantX/bridge/bridgeY/cmd/cmd-1"
    msg.payload = json.dumps({"id": "cmd-1", "action": "on"}).encode()

    transport._on_message(None, None, msg)
    assert enqueued == [{"id": "cmd-1", "action": "on"}]


def test_on_message_ignores_own_ack_echo(transport):
    """A cmd/{id}/ack message (our own echo) is ignored, not enqueued."""
    enqueued = []
    transport._enqueue_command = enqueued.append

    msg = MagicMock()
    msg.topic = "ga/tenantX/bridge/bridgeY/cmd/cmd-1/ack"
    msg.payload = json.dumps({"id": "cmd-1", "success": True}).encode()

    transport._on_message(None, None, msg)
    assert enqueued == []


def test_on_message_automations_logged_only(transport):
    """An automations message is logged only, never enqueued."""
    enqueued = []
    transport._enqueue_command = enqueued.append

    msg = MagicMock()
    msg.topic = "ga/tenantX/bridge/bridgeY/automations"
    msg.payload = json.dumps({"rules": []}).encode()

    transport._on_message(None, None, msg)
    assert enqueued == []


def test_on_disconnect_does_not_loop_stop_inline(transport):
    """on_disconnect (paho thread) must NOT call loop_stop() inline — that would
    join the network thread from itself. It only flips the flag and schedules
    teardown."""
    transport._connected = True
    scheduled = []
    transport._schedule = lambda coro: scheduled.append(coro) or coro.close()

    transport._on_disconnect(None, None)

    assert transport._connected is False
    # loop_stop must not be touched on the paho thread.
    transport._client.loop_stop.assert_not_called()
    # A teardown coroutine was scheduled onto the loop instead.
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_teardown_client_stops_loop(transport):
    """_teardown_client (runs on the loop) stops the paho loop and drops it."""
    client = transport._client
    await transport._teardown_client()
    client.loop_stop.assert_called_once()
    assert transport._client is None


def test_is_not_authorized_detection():
    """rc 5 and 'Not authorized' reason codes are detected; others are not."""
    assert MqttTransport._is_not_authorized(5) is True

    rc = MagicMock()
    rc.__int__ = lambda self: 5
    assert MqttTransport._is_not_authorized(rc) is True

    name_rc = MagicMock()
    name_rc.__int__ = lambda self: 999
    name_rc.getName = lambda: "Not authorized"
    assert MqttTransport._is_not_authorized(name_rc) is True

    ok_rc = MagicMock()
    ok_rc.__int__ = lambda self: 0
    ok_rc.getName = lambda: "Success"
    assert MqttTransport._is_not_authorized(ok_rc) is False

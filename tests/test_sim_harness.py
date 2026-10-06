"""The grow simulator's harness drives the real automation stack on a virtual clock."""

import json
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = "Europe/Amsterdam"


def _ms(local: str) -> int:
    return int(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(TZ)).timestamp() * 1000)


class Harness:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "sim.harness"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def send(self, req: dict) -> dict:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        assert reply["ok"], reply
        return reply

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


DEVICES = [
    {
        "name": "tent_temp",
        "deviceType": "temperature",
        "category": "sensor",
        "metadata": {"unit": "°C"},
    },
    {
        "name": "grow_light",
        "deviceType": "light",
        "category": "actuator",
        "capabilities": ["on", "off"],
    },
    {"name": "pump", "deviceType": "pump", "category": "actuator", "capabilities": ["on", "off"]},
]


def _rules(version: int, automations: list) -> dict:
    return {"op": "rules", "payload": json.dumps({"automations": automations, "version": version})}


def test_manifest_comes_from_the_real_registry():
    h = Harness()
    try:
        reply = h.send(
            {"op": "init", "tz": TZ, "startMs": _ms("2026-03-01T05:58:00"), "devices": DEVICES}
        )
        ids = [d["entityId"] for d in reply["manifest"]["devices"]]
        assert ids == ["sim.grow_light", "sim.pump", "sim.tent_temp"]
        light = next(d for d in reply["manifest"]["devices"] if d["name"] == "grow_light")
        assert light["entityDomain"] == "light" and light["deviceClass"] == "light"
        assert reply["state"]["manifestHash"]
    finally:
        h.close()


def test_time_trigger_fires_on_virtual_minute_and_echoes():
    h = Harness()
    try:
        h.send({"op": "init", "tz": TZ, "startMs": _ms("2026-03-01T05:58:00"), "devices": DEVICES})
        status = h.send(
            _rules(
                1,
                [
                    {
                        "id": "a1",
                        "name": "lights on",
                        "enabled": True,
                        "triggers": [{"type": "time", "at": "06:00"}],
                        "conditions": [],
                        "actions": [
                            {"type": "call", "service": "turn_on", "entity": "sim.grow_light"}
                        ],
                    }
                ],
            )
        )
        assert status["events"][0]["kind"] == "status"
        assert status["events"][0]["payload"]["ok"] is True

        before = h.send({"op": "advance", "toMs": _ms("2026-03-01T05:59:00")})
        assert not [e for e in before["events"] if e["kind"] == "device"]

        after = h.send({"op": "advance", "toMs": _ms("2026-03-01T06:01:00")})
        devices = [e for e in after["events"] if e["kind"] == "device"]
        assert devices[0]["name"] == "grow_light" and devices[0]["action"] == "on"
        assert devices[0]["atMs"] == _ms("2026-03-01T06:00:00")
        fired = [e for e in after["events"] if e["kind"] == "fired"]
        assert fired[0]["payload"]["automationId"] == "a1"
        assert fired[0]["payload"]["firedAt"].startswith("2026-03-01T05:00:00")
    finally:
        h.close()


def test_event_command_runs_a_delayed_pump_sequence():
    h = Harness()
    try:
        h.send({"op": "init", "tz": TZ, "startMs": _ms("2026-03-01T10:00:00"), "devices": DEVICES})
        h.send(
            _rules(
                1,
                [
                    {
                        "id": "w1",
                        "name": "water",
                        "enabled": True,
                        "triggers": [{"type": "event", "event_type": "ga.water"}],
                        "conditions": [],
                        "actions": [
                            {"type": "call", "service": "turn_on", "entity": "sim.pump"},
                            {"type": "delay", "seconds": 90},
                            {"type": "call", "service": "turn_off", "entity": "sim.pump"},
                        ],
                    }
                ],
            )
        )
        ack = h.send(
            {
                "op": "command",
                "command": {
                    "id": "c1",
                    "targetType": "event",
                    "targetId": "ga.water",
                    "action": "fire",
                    "payload": {"data": {"zone": "z"}},
                },
            }
        )
        assert ack["ack"] == {"id": "c1", "success": True, "message": "Event fired"}
        assert [e["action"] for e in ack["events"] if e["kind"] == "device"] == ["on"]

        later = h.send({"op": "advance", "toMs": _ms("2026-03-01T10:03:00")})
        off = [e for e in later["events"] if e["kind"] == "device"]
        assert off[0]["action"] == "off" and off[0]["atMs"] == _ms("2026-03-01T10:01:30")
    finally:
        h.close()


def test_samples_are_calibrated_and_failing_devices_reject_commands():
    h = Harness()
    try:
        h.send({"op": "init", "tz": TZ, "startMs": _ms("2026-03-01T10:00:00"), "devices": DEVICES})
        h.send(
            {
                "op": "rules",
                "payload": json.dumps(
                    {
                        "automations": [],
                        "version": 1,
                        "calibrations": {"sim.tent_temp": {"offset": -1.5, "scale": 1}},
                    }
                ),
            }
        )
        reply = h.send(
            {
                "op": "advance",
                "toMs": _ms("2026-03-01T10:01:00"),
                "samples": [{"name": "tent_temp", "value": 25.0}],
            }
        )
        assert reply["telemetry"][0]["entityId"] == "sim.tent_temp"
        assert reply["telemetry"][0]["value"] == 23.5

        h.send({"op": "fault", "name": "pump", "failing": True})
        ack = h.send(
            {
                "op": "command",
                "command": {
                    "id": "c2",
                    "targetType": "actuator",
                    "targetId": "sim.pump",
                    "action": "on",
                },
            }
        )
        assert ack["ack"]["success"] is False
    finally:
        h.close()

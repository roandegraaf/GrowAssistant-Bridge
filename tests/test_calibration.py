"""Sensor calibration: parsing, the manager's three parse sites, and the
end-to-end effect on both the published telemetry and the engine's evaluation."""

import json
import threading
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.automations import AutomationManager
from app.automations.engine import AutomationEngine
from app.automations.event_bus import EventBus
from app.automations.executor import ActionExecutor
from app.automations.state_store import StateStore
from app.calibration import Calibration, calibrate, parse_calibrations
from app.main import Application
from app.registry import DeviceCategory, registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry.clear()
    yield
    registry.clear()


def _ruleset(automations, version, calibrations=None) -> bytes:
    body = {"automations": automations, "version": version}
    if calibrations is not None:
        body["calibrations"] = calibrations
    return json.dumps(body).encode("utf-8")


class TestParseAndApply:
    def test_parse_skips_malformed_entries(self):
        parsed = parse_calibrations(
            {
                "sensor.temp": {"offset": -0.5},
                "sensor.rh": {"offset": 2, "scale": 1.1},
                "sensor.bad_scale": {"scale": 0},
                "sensor.negative_scale": {"scale": -1},
                "sensor.bool": {"offset": True},
                "sensor.nan": {"offset": float("nan")},
                "no_domain": {"offset": 1},
                "sensor.not_a_dict": 3,
            }
        )
        assert parsed == {
            "sensor.temp": Calibration(-0.5, 1.0),
            "sensor.rh": Calibration(2.0, 1.1),
        }
        assert parse_calibrations(None) == {}
        assert parse_calibrations([1, 2]) == {}

    def test_calibrate_numbers_and_numeric_strings_only(self):
        cal = Calibration(offset=0.3, scale=2.0)
        assert calibrate(10, cal) == 20.3
        assert calibrate(21.7, Calibration(0.1)) == 21.8
        assert calibrate("10", cal) == 20.3
        assert calibrate("on", cal) == "on"
        assert calibrate(True, cal) is True
        assert calibrate(None, cal) is None
        assert calibrate(10, None) == 10


class TestManagerParseSites:
    async def test_newer_payload_sets_and_clear_resets(self):
        mgr = AutomationManager()
        await mgr.apply_payload(_ruleset([], 1, {"sensor.temp": {"offset": 1}}))
        assert mgr.calibrations == {"sensor.temp": Calibration(1.0, 1.0)}

        await mgr.apply_payload(_ruleset([], 1, {"sensor.temp": {"offset": 9}}))
        assert mgr.calibrations["sensor.temp"].offset == 1.0  # same version → ignored

        await mgr.apply_payload(_ruleset([], 2))
        assert mgr.calibrations == {}  # newer set without calibrations clears them

        await mgr.apply_payload(_ruleset([], 3, {"sensor.temp": {"offset": 1}}))
        await mgr.apply_payload(b"")
        assert mgr.calibrations == {}

    def test_restores_calibrations_from_cache(self, monkeypatch):
        payload = {"automations": [], "version": 4, "calibrations": {"sensor.rh": {"scale": 1.2}}}
        cached = {"payload": json.dumps(payload), "version": 4}
        monkeypatch.setattr("app.automations.manager.config_store.get_config", lambda key: cached)
        assert AutomationManager().calibrations == {"sensor.rh": Calibration(0.0, 1.2)}


class FakeFan:
    def __init__(self):
        self.calls = []

    async def execute_command(self, target_id, action, payload):
        self.calls.append((target_id, action))
        return True


@pytest.fixture
def application():
    Application._instance = None
    Application._lock = threading.Lock()
    with patch("app.main.signal.signal"):
        yield Application()
    Application._instance = None
    Application._lock = threading.Lock()


class TestCalibrationEndToEnd:
    async def test_offset_changes_reported_value_and_engine_evaluation(self, application):
        registry.register_device(
            name="temp",
            domain="sensor",
            device_type="temperature",
            category=DeviceCategory.SENSOR,
            integration_name="Probe",
        )
        registry.register_device(
            name="fan",
            domain="switch",
            device_type="fan",
            category=DeviceCategory.ACTUATOR,
            integration_name="FakeFan",
        )
        fan = FakeFan()
        store = StateStore()
        engine = AutomationEngine(
            store,
            EventBus(),
            ActionExecutor(lambda n: {"FakeFan": fan}.get(n), state_store=store),
            now=lambda: datetime(2026, 1, 1, 12, 0, 0),
            scheduler_interval=3600,
        )
        manager = AutomationManager()
        manager.set_engine(engine)
        rule = {
            "id": "r",
            "enabled": True,
            "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
            "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
        }
        await manager.apply_payload(_ruleset([rule], 1, {"sensor.temp": {"offset": -5}}))
        engine.start()

        application._automations = manager
        application._state_store = store
        application._engine = engine

        put = AsyncMock()
        try:
            with patch("app.main.queue_manager.put", put):
                for raw in (32, 34):
                    await application._collect_sample(
                        "Probe", {"entity_id": "sensor.temp", "value": raw}, 0
                    )
                    await engine.join()
                assert fan.calls == []  # raw crossed 30, calibrated (27, 29) did not

                await application._collect_sample(
                    "Probe", {"entity_id": "sensor.temp", "value": 36}, 0
                )
                await engine.join()
                assert fan.calls == [("fan", "on")]
        finally:
            await engine.stop()

        assert [c.args[0]["value"] for c in put.await_args_list] == [27, 29, 31]
        assert store.get("sensor.temp") == 31

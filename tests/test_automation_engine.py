"""Tests for the AutomationEngine — the trigger/condition/action runtime.

Covers every trigger type (state/numeric_state/time/time_pattern/event), every
condition (state/numeric_state/time/and/or/not) and every action
(call/delay/wait_for_state/set_variable/fire_event), plus the load-bearing
behaviours: edge-triggering (no fan-storm on a hot restart), single run mode,
the fire_event loop guard, and end-to-end event/set_variable/fire_event flows.
"""

import asyncio
import logging
from datetime import datetime

import pytest

from app.automations.engine import (
    AutomationEngine,
    numeric_range_match,
    state_equals,
    time_condition_matches,
    time_pattern_matches,
    time_trigger_matches,
)
from app.automations.event_bus import EventBus
from app.automations.executor import ActionExecutor
from app.automations.state_store import StateStore
from app.registry import DeviceCategory, registry

FIXED_NOON = datetime(2026, 1, 1, 12, 0, 0)  # a Thursday


@pytest.fixture(autouse=True)
def clean_registry():
    registry.clear()
    yield
    registry.clear()


class FakeIntegration:
    def __init__(self):
        self.calls = []

    async def execute_command(self, target_id, action, payload):
        self.calls.append((target_id, action, payload))
        return True


def _register(entity_id, category=DeviceCategory.ACTUATOR):
    domain, name = entity_id.split(".", 1)
    registry.register_device(
        name=name,
        domain=domain,
        device_type="fan",
        category=category,
        integration_name="FakeIntegration",
    )


async def _noop_sleep(_seconds):
    return None


def _build(now=None, sleep=None):
    store = StateStore()
    bus = EventBus()
    fake = FakeIntegration()
    integrations = {"FakeIntegration": fake}
    executor = ActionExecutor(lambda n: integrations.get(n), state_store=store)
    engine = AutomationEngine(
        store,
        bus,
        executor,
        now=now or (lambda: FIXED_NOON),
        sleep=sleep or _noop_sleep,
        scheduler_interval=3600,
    )
    return engine, store, bus, fake


# ─── Pure matching helpers ──────────────────────────────────────────────────


class TestPureMatchers:
    def test_numeric_range(self):
        assert numeric_range_match(35, 30, None) is True
        assert numeric_range_match(25, 30, None) is False
        assert numeric_range_match(15, 10, 20) is True
        assert numeric_range_match("nan-ish", 10, None) is False

    def test_time_trigger(self):
        assert time_trigger_matches({"at": "06:30"}, datetime(2026, 1, 1, 6, 30, 0)) is True
        assert time_trigger_matches({"at": "06:30"}, datetime(2026, 1, 1, 6, 30, 5)) is False
        assert time_trigger_matches({"at": "06:30:05"}, datetime(2026, 1, 1, 6, 30, 5)) is True

    def test_time_pattern_every_5_minutes_at_second_zero(self):
        assert time_pattern_matches({"minutes": "/5"}, datetime(2026, 1, 1, 6, 10, 0)) is True
        assert time_pattern_matches({"minutes": "/5"}, datetime(2026, 1, 1, 6, 11, 0)) is False
        # seconds default to 0 when only minutes is specified
        assert time_pattern_matches({"minutes": "/5"}, datetime(2026, 1, 1, 6, 10, 30)) is False

    def test_time_pattern_hours_step(self):
        assert time_pattern_matches({"hours": "/2"}, datetime(2026, 1, 1, 4, 0, 0)) is True
        assert time_pattern_matches({"hours": "/2"}, datetime(2026, 1, 1, 4, 5, 0)) is False

    def test_state_equals_binary_synonyms(self):
        # The app's flow builder writes canonical "on"/"off"; integrations
        # report whatever their hardware yields (GPIO 1/0, MQTT "on",
        # ESPHome True). All spellings of a binary state must compare equal.
        assert state_equals(1, "on") is True
        assert state_equals(0, "off") is True
        assert state_equals(True, "on") is True
        assert state_equals(False, "off") is True
        assert state_equals("On", "on") is True
        assert state_equals("open", "on") is True
        assert state_equals("closed", "off") is True
        assert state_equals("yes", "1") is True
        assert state_equals(1.0, "on") is True
        assert state_equals("on", "off") is False
        assert state_equals(1, "off") is False

    def test_state_equals_numeric_and_plain_strings(self):
        # Numeric strings compare by value; other strings case-insensitively.
        assert state_equals(21.5, "21.5") is True
        assert state_equals("21.50", 21.5) is True
        assert state_equals("Eco", "eco") is True
        assert state_equals("eco", "boost") is False
        assert state_equals(2, "on") is False

    def test_time_condition_window_and_weekday(self):
        c = {"after": "06:00", "before": "22:00"}
        assert time_condition_matches(c, FIXED_NOON) is True
        assert time_condition_matches(c, datetime(2026, 1, 1, 5, 0, 0)) is False
        # window wrapping midnight
        wrap = {"after": "22:00", "before": "06:00"}
        assert time_condition_matches(wrap, datetime(2026, 1, 1, 23, 0, 0)) is True
        assert time_condition_matches(wrap, datetime(2026, 1, 1, 12, 0, 0)) is False
        # weekday: FIXED_NOON is a Thursday (weekday 3)
        assert time_condition_matches({"weekday": [3]}, FIXED_NOON) is True
        assert time_condition_matches({"weekday": [0]}, FIXED_NOON) is False


# ─── numeric_state / state triggers (edge detection) ────────────────────────


class TestNumericStateTrigger:
    async def test_fires_on_crossing_not_every_sample_and_not_on_baseline(self):
        engine, store, _bus, fake = _build()
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.temp", 20)  # first sample → baseline only
            await engine.join()
            assert fake.calls == []

            await store.set("sensor.temp", 35)  # cross into >30 → fire
            await engine.join()
            assert fake.calls == [("fan", "on", {})]

            await store.set("sensor.temp", 36)  # still hot → no new edge
            await engine.join()
            assert len(fake.calls) == 1

            await store.set("sensor.temp", 25)  # leave
            await store.set("sensor.temp", 33)  # re-enter → fire again
            await engine.join()
            assert len(fake.calls) == 2
        finally:
            await engine.stop()

    async def test_first_sample_already_hot_does_not_fire(self):
        engine, store, _bus, fake = _build()
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.temp", 35)  # already hot at boot → baseline, no fire
            await store.set("sensor.temp", 36)  # still hot → no edge
            await engine.join()
            assert fake.calls == []
        finally:
            await engine.stop()


class TestStateTrigger:
    async def test_fires_on_transition_to_target(self):
        engine, store, _bus, fake = _build()
        _register("sensor.door", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "state", "entity": "sensor.door", "to": "open"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.door", "closed")  # baseline
            await store.set("sensor.door", "open")  # → fire
            await store.set("sensor.door", "open")  # no change → no re-fire
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_binary_synonyms_fire_canonical_to_on(self):
        # A GPIO-style switch reports 1/0; the builder's trigger says `to: "on"`.
        engine, store, _bus, fake = _build()
        _register("switch.pump", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "state", "entity": "switch.pump", "to": "on"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("switch.pump", 0)  # baseline
            await store.set("switch.pump", 1)  # 1 ≡ "on" → fire
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_respelling_same_state_is_not_a_change(self):
        # An integration report of "on" followed by a write-back of 1 is the
        # same canonical state — it must not re-fire the trigger.
        engine, store, _bus, fake = _build()
        _register("switch.pump", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "state", "entity": "switch.pump", "to": "on"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("switch.pump", "off")  # baseline
            await store.set("switch.pump", "on")  # → fire
            await store.set("switch.pump", 1)  # re-spelling, not a change
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_from_constraint_must_match_previous_state(self):
        engine, store, _bus, fake = _build()
        _register("sensor.door", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [
                        {"type": "state", "entity": "sensor.door", "from": "closed", "to": "open"}
                    ],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.door", "ajar")  # baseline
            await store.set("sensor.door", "open")  # from 'ajar' ≠ 'closed' → no fire
            await engine.join()
            assert fake.calls == []
            await store.set("sensor.door", "closed")
            await store.set("sensor.door", "open")  # from 'closed' → fire
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()


# ─── time / time_pattern triggers (via scheduler tick) ──────────────────────


class TestTimeTriggers:
    async def test_time_fires_once_per_occurrence(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "time", "at": "06:30"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 30, 0))
        await engine.join()
        assert len(fake.calls) == 1
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 30, 0))  # same instant → no double
        await engine.join()
        assert len(fake.calls) == 1
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 31, 0))  # different minute → no
        await engine.join()
        assert len(fake.calls) == 1

    async def test_time_pattern_every_5_minutes(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "time_pattern", "minutes": "/5"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        # join between ticks so each run completes (real ticks are 1s apart);
        # otherwise single run-mode would suppress the next while the first runs.
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 10, 0))
        await engine.join()
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 11, 0))  # not a multiple
        await engine.join()
        engine._scheduler_tick(datetime(2026, 1, 1, 6, 15, 0))
        await engine.join()
        assert len(fake.calls) == 2


# ─── event triggers + fire_event ────────────────────────────────────────────


class TestEvents:
    async def test_event_trigger_and_fire_event_chain_end_to_end(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "a",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "ping"}],
                    "actions": [{"type": "fire_event", "event_type": "pong"}],
                },
                {
                    "id": "b",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "pong"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                },
            ]
        )
        engine.start()
        try:
            engine.emit_event("ping")  # ping → fire_event pong → call
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_event_data_must_match(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [
                        {"type": "event", "event_type": "x", "event_data": {"zone": "tent1"}}
                    ],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("x", {"zone": "tent2"})  # data mismatch → no fire
            await engine.join()
            assert fake.calls == []
            engine.emit_event("x", {"zone": "tent1", "extra": 1})  # superset matches
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_fire_event_dedupes_identical_events_within_a_tick(self):
        engine, _store, bus, _fake = _build()
        done = []
        bus.subscribe(lambda t, d, m: done.append(t) if t == "done" else None)
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {"type": "fire_event", "event_type": "done"},
                        {"type": "fire_event", "event_type": "done"},  # identical → deduped
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
            assert done == ["done"]
        finally:
            await engine.stop()

    async def test_fire_event_depth_guard_caps_a_unique_event_chain(self):
        # Two rules ping-pong (A→eb→B→ea→A…) with unique data each hop, so neither
        # dedupe (data differs) nor single run-mode (different rules) stops it —
        # only the depth cap can. Confirms a runaway fire_event chain is bounded.
        engine, _store, bus, _fake = _build()
        ns = []
        bus.subscribe(lambda t, d, m: ns.append(d.get("n")) if t in ("ea", "eb") else None)
        incr = {"n": "{{ trigger['event_data']['n'] + 1 }}"}
        engine.apply_rules(
            [
                {
                    "id": "a",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "ea"}],
                    "actions": [{"type": "fire_event", "event_type": "eb", "event_data": incr}],
                },
                {
                    "id": "b",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "eb"}],
                    "actions": [{"type": "fire_event", "event_type": "ea", "event_data": incr}],
                },
            ]
        )
        engine.start()
        try:
            engine.emit_event("ea", {"n": 0})
            await engine.join()
            # External n=0 plus depth-1..9 hops (n=1..9) = 10 emits, then dropped.
            assert ns == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        finally:
            await engine.stop()


# ─── run mode ───────────────────────────────────────────────────────────────


class TestRunModeSingle:
    async def test_new_trigger_ignored_while_action_sequence_running(self):
        gate = asyncio.Event()

        async def gated_sleep(_seconds):
            await gate.wait()

        engine, store, _bus, fake = _build(sleep=gated_sleep)
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
                    "actions": [
                        {"type": "delay", "seconds": 600},
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.temp", 20)  # baseline
            await store.set("sensor.temp", 35)  # fire → run reaches the gated delay
            await asyncio.sleep(0)
            await store.set("sensor.temp", 25)  # leave
            await store.set("sensor.temp", 36)  # re-enter while run active → ignored (single)
            await asyncio.sleep(0)
            gate.set()  # let the first (only) run finish
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            gate.set()
            await engine.stop()


# ─── conditions ─────────────────────────────────────────────────────────────


class TestConditions:
    def _engine_with_state(self, **values):
        engine, store, _bus, _fake = _build()
        for k, v in values.items():
            store._values[k] = v  # seed directly (sync) for condition evaluation
        return engine

    def test_state_numeric_time_and_or_not(self):
        engine = self._engine_with_state(**{"switch.mode": "auto", "sensor.temp": 35})
        now = FIXED_NOON

        assert (
            engine._evaluate_condition(
                {"type": "state", "entity": "switch.mode", "state": "auto"}, now
            )
            is True
        )
        assert (
            engine._evaluate_condition(
                {"type": "state", "entity": "switch.mode", "state": "off"}, now
            )
            is False
        )
        assert (
            engine._evaluate_condition(
                {"type": "state", "entity": "switch.ghost", "state": "x"}, now
            )
            is False
        )

        assert (
            engine._evaluate_condition(
                {"type": "numeric_state", "entity": "sensor.temp", "above": 30}, now
            )
            is True
        )
        assert (
            engine._evaluate_condition(
                {"type": "numeric_state", "entity": "sensor.temp", "below": 30}, now
            )
            is False
        )

        assert (
            engine._evaluate_condition({"type": "time", "after": "06:00", "before": "22:00"}, now)
            is True
        )

        and_c = {
            "type": "and",
            "conditions": [
                {"type": "state", "entity": "switch.mode", "state": "auto"},
                {"type": "numeric_state", "entity": "sensor.temp", "above": 30},
            ],
        }
        assert engine._evaluate_condition(and_c, now) is True

        or_c = {
            "type": "or",
            "conditions": [
                {"type": "state", "entity": "switch.mode", "state": "off"},
                {"type": "numeric_state", "entity": "sensor.temp", "above": 30},
            ],
        }
        assert engine._evaluate_condition(or_c, now) is True

        not_c = {
            "type": "not",
            "conditions": [{"type": "numeric_state", "entity": "sensor.temp", "below": 30}],
        }
        assert engine._evaluate_condition(not_c, now) is True

    async def test_failing_condition_blocks_actions(self):
        engine, store, _bus, fake = _build()
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        await store.set("switch.mode", "off")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
                    "conditions": [{"type": "state", "entity": "switch.mode", "state": "auto"}],
                    "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
                }
            ]
        )
        engine.start()
        try:
            await store.set("sensor.temp", 20)
            await store.set("sensor.temp", 35)  # trigger fires but condition (mode=auto) fails
            await engine.join()
            assert fake.calls == []
        finally:
            await engine.stop()


# ─── actions: delay / set_variable / wait_for_state ─────────────────────────


class TestActions:
    async def test_delay_runs_actions_in_order(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                        {"type": "delay", "seconds": 600},
                        {"type": "call", "entity": "switch.fan", "service": "turn_off"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
            assert fake.calls == [("fan", "on", {}), ("fan", "off", {})]
        finally:
            await engine.stop()

    async def test_set_variable_template_flows_into_call_payload(self):
        engine, store, _bus, fake = _build()
        _register("number.target")
        await store.set("sensor.temp", 22)
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {
                            "type": "set_variable",
                            "name": "target",
                            "value_template": "{{ states['sensor.temp'] }}",
                        },
                        {
                            "type": "call",
                            "entity": "number.target",
                            "service": "set_value",
                            "data": {"value": "{{ variables['target'] }}"},
                        },
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
            assert fake.calls == [("target", "set", {"value": 22})]
        finally:
            await engine.stop()

    async def test_wait_for_state_resumes_when_value_arrives(self):
        engine, store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {
                            "type": "wait_for_state",
                            "entity": "sensor.ready",
                            "state": "1",
                            "timeout": 5,
                        },
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await asyncio.sleep(0.02)  # run suspends in wait_for_state
            assert fake.calls == []
            await store.set("sensor.ready", "1")  # change-notification resumes it
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_wait_for_state_continues_after_timeout(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {
                            "type": "wait_for_state",
                            "entity": "sensor.never",
                            "state": "1",
                            "timeout": 0.02,
                        },
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()  # times out, then continues to the call
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_unknown_action_type_is_skipped_and_sequence_continues(self):
        # An unrecognised action is skipped (logged), and later actions still run
        # — the existing "unknown action" behaviour, unchanged by notification.
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {"type": "explode"},  # unknown → skipped, no crash
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()


# ─── notification action ─────────────────────────────────────────────────────


class TestNotificationAction:
    async def test_renders_templates_and_calls_publisher_with_full_payload(self):
        engine, store, _bus, _fake = _build()
        await store.set("sensor.temp", 29)
        published = []

        async def publisher(payload):
            published.append(payload)

        engine.set_notify_publisher(publisher)
        engine.apply_rules(
            [
                {
                    "id": "notify-1",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {
                            "type": "notification",
                            "title": "Tent is {{ states['sensor.temp'] }}°C",
                            "message": "High temperature in the tent",
                        }
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()

        assert len(published) == 1
        payload = published[0]
        assert payload["automationId"] == "notify-1"
        assert payload["title"] == "Tent is 29°C"  # embedded template rendered
        assert payload["message"] == "High temperature in the tent"
        # firedAt is an ISO-8601 UTC timestamp (same format as the status echo).
        fired = datetime.fromisoformat(payload["firedAt"])
        assert fired.tzinfo is not None

    async def test_no_publisher_wired_logs_warning_and_does_not_crash(self, caplog):
        engine, _store, _bus, _fake = _build()  # notify publisher left unset
        engine.apply_rules(
            [
                {
                    "id": "notify-2",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [{"type": "notification", "title": "hi", "message": "there"}],
                }
            ]
        )
        engine.start()
        try:
            with caplog.at_level(logging.WARNING):
                engine.emit_event("go")
                await engine.join()  # must not raise
        finally:
            await engine.stop()
        assert "no notify publisher wired" in caplog.text

    async def test_failed_template_falls_back_to_raw_value(self):
        engine, _store, _bus, _fake = _build()
        published = []

        async def publisher(payload):
            published.append(payload)

        engine.set_notify_publisher(publisher)
        engine.apply_rules(
            [
                {
                    "id": "notify-3",
                    "enabled": True,
                    "triggers": [{"type": "event", "event_type": "go"}],
                    "actions": [
                        {
                            "type": "notification",
                            "title": "Alert {{ bogus_name }}",  # unknown name → render fails
                            "message": "ok",
                        }
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()  # a failed render must not crash the run
        finally:
            await engine.stop()

        assert len(published) == 1
        # On a failed template the field falls back to its raw value (mirrors _render_data).
        assert published[0]["title"] == "Alert {{ bogus_name }}"
        assert published[0]["message"] == "ok"


# ─── fired echo (…/automations/fired) ────────────────────────────────────────


class TestFiredEcho:
    """Every completed fire — conditions passed, actions ran — is echoed to the
    fired publisher with its result; a conditions-gated trigger is not a fire."""

    def _fired_rule(self, actions):
        return {
            "id": "rule-1",
            "enabled": True,
            "triggers": [{"type": "event", "event_type": "go"}],
            "actions": actions,
        }

    async def _run(self, engine):
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()

    async def test_successful_fire_publishes_ok_true(self):
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        published = []

        async def fired_publisher(payload):
            published.append(payload)

        engine.set_fired_publisher(fired_publisher)
        engine.apply_rules(
            [self._fired_rule([{"type": "call", "entity": "switch.fan", "service": "turn_on"}])]
        )
        await self._run(engine)

        assert fake.calls == [("fan", "on", {})]
        assert len(published) == 1
        payload = published[0]
        assert payload["automationId"] == "rule-1"
        assert payload["ok"] is True
        assert payload["error"] is None
        fired = datetime.fromisoformat(payload["firedAt"])
        assert fired.tzinfo is not None

    async def test_conditions_gated_trigger_publishes_nothing(self):
        engine, store, _bus, _fake = _build()
        await store.set("sensor.mode", "day")
        published = []

        async def fired_publisher(payload):
            published.append(payload)

        engine.set_fired_publisher(fired_publisher)
        rule = self._fired_rule([{"type": "notification", "title": "t", "message": "m"}])
        rule["conditions"] = [{"type": "state", "entity": "sensor.mode", "state": "night"}]
        engine.apply_rules([rule])
        await self._run(engine)

        assert published == []  # gated, not a fire

    async def test_failed_call_publishes_ok_false_with_first_error(self):
        # switch.ghost is never registered → the executor returns False; the
        # sequence continues (switch.fan still runs) and the echo carries the
        # FIRST failure.
        engine, _store, _bus, fake = _build()
        _register("switch.fan")
        published = []

        async def fired_publisher(payload):
            published.append(payload)

        engine.set_fired_publisher(fired_publisher)
        engine.apply_rules(
            [
                self._fired_rule(
                    [
                        {"type": "call", "entity": "switch.ghost", "service": "turn_on"},
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ]
                )
            ]
        )
        await self._run(engine)

        assert fake.calls == [("fan", "on", {})]  # later actions still ran
        assert len(published) == 1
        payload = published[0]
        assert payload["ok"] is False
        assert "switch.ghost" in payload["error"]

    async def test_exception_mid_sequence_publishes_ok_false(self):
        # A notify publisher that raises propagates out of the notification
        # action — the run aborts, and the fired echo reports the exception.
        engine, _store, _bus, _fake = _build()
        published = []

        async def bad_notify(_payload):
            raise RuntimeError("push exploded")

        async def fired_publisher(payload):
            published.append(payload)

        engine.set_notify_publisher(bad_notify)
        engine.set_fired_publisher(fired_publisher)
        engine.apply_rules(
            [self._fired_rule([{"type": "notification", "title": "t", "message": "m"}])]
        )
        await self._run(engine)

        assert len(published) == 1
        payload = published[0]
        assert payload["ok"] is False
        assert "push exploded" in payload["error"]

    async def test_fired_publisher_error_is_isolated(self, caplog):
        # A broken fired publisher must never break rule execution (or leave the
        # rule stuck "active" — a second trigger must still fire).
        engine, _store, _bus, fake = _build()
        _register("switch.fan")

        async def bad_fired(_payload):
            raise RuntimeError("echo down")

        engine.set_fired_publisher(bad_fired)
        engine.apply_rules(
            [self._fired_rule([{"type": "call", "entity": "switch.fan", "service": "turn_on"}])]
        )
        engine.start()
        try:
            with caplog.at_level(logging.ERROR):
                engine.emit_event("go")
                await engine.join()
                engine.emit_event("go")
                await engine.join()
        finally:
            await engine.stop()

        assert fake.calls == [("fan", "on", {}), ("fan", "on", {})]
        assert "Fired echo publish failed" in caplog.text

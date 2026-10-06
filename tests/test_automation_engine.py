"""Tests for the AutomationEngine — the trigger/condition/action runtime.

Covers every trigger type (state/numeric_state/time/time_pattern/event), every
condition (state/numeric_state/time/and/or/not) and every action
(call/delay/wait_for_state/set_variable/fire_event), plus the load-bearing
behaviours: edge-triggering (no fan-storm on a hot restart), single run mode,
the fire_event loop guard, and end-to-end event/set_variable/fire_event flows.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

import pytest

from app.automations.engine import (
    HOLD_TICK_SECONDS,
    AutomationEngine,
    hold_wants_on,
    numeric_range_match,
    state_equals,
    time_condition_matches,
    time_pattern_matches,
    time_trigger_matches,
)
from app.automations.event_bus import EventBus
from app.automations.executor import ActionExecutor
from app.automations.metrics import DliAccumulator, dew_point_c, vpd_kpa
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

    def test_time_trigger_every_n_days(self):
        trig = {"at": "08:00", "every_days": 3, "starting": "2026-01-01"}
        fires = [
            d for d in range(1, 11) if time_trigger_matches(trig, datetime(2026, 1, d, 8, 0, 0))
        ]
        assert fires == [1, 4, 7, 10]
        assert time_trigger_matches(trig, datetime(2025, 12, 29, 8, 0, 0)) is False

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

    async def test_reapply_rules_preserves_baseline_across_unrelated_edit(self):
        # Regression: apply_rules used to clear every edge-detection baseline,
        # so a rule-set change (e.g. editing an unrelated rule) made the next
        # sample look like first_seen and swallowed a genuine crossing. Seeding
        # from the StateStore snapshot must keep the baseline intact.
        engine, store, _bus, fake = _build()
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        rule = {
            "id": "r",
            "enabled": True,
            "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
            "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
        }
        engine.apply_rules([rule])
        engine.start()
        try:
            await store.set("sensor.temp", 20)  # baseline recorded at 20
            await engine.join()
            assert fake.calls == []

            # A rule-set is re-applied (same or unrelated edit) while temp sits
            # below the threshold. The baseline must remain 20, not be cleared.
            engine.apply_rules([rule])

            await store.set("sensor.temp", 35)  # 20 → 35 crosses 30 → must fire
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_reapply_when_already_hot_still_does_not_fire(self):
        # The reseed must also preserve the "don't fire on the first sample
        # after apply" intent: if the sensor is already above the threshold at
        # apply time, re-applying must not fire on the next equal-ish sample.
        engine, store, _bus, fake = _build()
        _register("sensor.temp", DeviceCategory.SENSOR)
        _register("switch.fan")
        rule = {
            "id": "r",
            "enabled": True,
            "triggers": [{"type": "numeric_state", "entity": "sensor.temp", "above": 30}],
            "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
        }
        engine.apply_rules([rule])
        engine.start()
        try:
            await store.set("sensor.temp", 35)  # already hot; baseline only
            await engine.join()
            assert fake.calls == []

            engine.apply_rules([rule])  # reseed baseline from store (35)
            await store.set("sensor.temp", 36)  # still hot → no new edge
            await engine.join()
            assert fake.calls == []
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


class TestDerivedMetrics:
    def test_vpd_and_dew_point_formulas(self):
        assert vpd_kpa(25, 60, 0) == pytest.approx(1.267, abs=1e-3)
        assert vpd_kpa(25, 60) < vpd_kpa(25, 60, 0)
        assert dew_point_c(25, 60) == pytest.approx(16.7, abs=0.05)
        assert dew_point_c(25, 100) == pytest.approx(25)
        assert dew_point_c(25, 0) is None
        assert dew_point_c(25, -5) is None

    def test_dli_constant_light_for_twelve_hours(self):
        acc = DliAccumulator()
        start = datetime(2026, 1, 1, 6, 0, 0)
        for minute in range(0, 12 * 60, 10):
            acc.add(500, start + timedelta(minutes=minute))
        acc.add(0, start + timedelta(hours=12))
        assert acc.value(start + timedelta(hours=13)) == pytest.approx(21.6)

    def test_dli_tail_gap_cap_and_non_numeric(self):
        acc = DliAccumulator()
        t0 = datetime(2026, 1, 1, 6, 0, 0)
        assert acc.value(t0) is None
        acc.add(1000, t0)
        acc.add("unavailable", t0 + timedelta(minutes=5))
        assert acc.value(t0 + timedelta(minutes=10)) == pytest.approx(0.6)
        assert acc.value(t0 + timedelta(minutes=31)) == pytest.approx(0)
        acc.add(1000, t0 + timedelta(hours=1))
        assert acc.value(t0 + timedelta(hours=1)) == pytest.approx(0)

    def test_dli_resets_at_midnight(self):
        acc = DliAccumulator()
        t0 = datetime(2026, 1, 1, 23, 50, 0)
        acc.add(1000, t0)
        acc.add(1000, t0 + timedelta(minutes=10))
        acc.add(1000, t0 + timedelta(minutes=20))
        assert acc.value(t0 + timedelta(minutes=20)) == pytest.approx(0.6)
        assert acc.value(datetime(2026, 1, 3, 12)) == pytest.approx(0)

    def _vpd(self, **extra):
        return {
            "type": "derived",
            "metric": "vpd",
            "temperature": "sensor.temp",
            "humidity": "sensor.rh",
            **extra,
        }

    def test_vpd_and_dew_point_conditions(self):
        engine, store, _bus, _fake = _build()
        store._values.update({"sensor.temp": 25, "sensor.rh": "60"})
        now = FIXED_NOON
        assert engine._evaluate_condition(self._vpd(leaf_offset=0, above=1.2, below=1.3), now)
        assert not engine._evaluate_condition(self._vpd(above=1.2), now)
        assert engine._evaluate_condition(self._vpd(below=1.0), now)
        dew = {**self._vpd(), "metric": "dew_point", "above": 16.5, "below": 17}
        assert engine._evaluate_condition(dew, now)
        assert not engine._evaluate_condition(self._vpd(humidity="sensor.ghost", below=9), now)
        store._values["sensor.rh"] = "unknown"
        assert not engine._evaluate_condition(self._vpd(below=9), now)

    async def test_dli_condition_fed_from_state_changes(self):
        clock = [datetime(2026, 1, 1, 6, 0, 0)]
        engine, store, _bus, _fake = _build(now=lambda: clock[0])
        dli = {"type": "derived", "metric": "dli", "light": "sensor.ppfd", "above": 0.5}
        engine.apply_rules(
            [
                {
                    "id": "r",
                    "triggers": [{"type": "time", "at": "20:00"}],
                    "conditions": [{"type": "not", "conditions": [dli]}],
                    "actions": [],
                }
            ]
        )
        engine.start()
        try:
            assert not engine._evaluate_condition(dli, clock[0])
            await store.set("sensor.ppfd", 1000)
            clock[0] += timedelta(minutes=10)
            assert engine._evaluate_condition(dli, clock[0])
            missing = {**dli, "light": "sensor.ghost", "above": None, "below": 99}
            assert not engine._evaluate_condition(missing, clock[0])
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

    async def test_wait_for_state_without_timeout_aborts_rest_on_expiry(self, monkeypatch):
        # With no user timeout, the injected default cap prevents a permanent
        # wedge — but on expiry the rule must ABORT (not run downstream actions
        # on a precondition that never held). Shrink the cap so the test is fast.
        monkeypatch.setattr("app.automations.engine.DEFAULT_WAIT_FOR_STATE_TIMEOUT", 0.02)
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
                            # no "timeout" → injected default → abort on expiry
                        },
                        {"type": "call", "entity": "switch.fan", "service": "turn_on"},
                    ],
                }
            ]
        )
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()  # injected cap elapses → rule aborts
            assert fake.calls == []  # downstream call must NOT run

            # The single-run lock must have been released — the rule can fire
            # again (it wasn't left wedged).
            engine.emit_event("go")
            await engine.join()
            assert fake.calls == []
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


# ─── climate_hold / ramp / stage ─────────────────────────────────────────────


class Clock:
    def __init__(self, start=FIXED_NOON):
        self.now = start

    def __call__(self):
        return self.now


def _scripted(clock, on_tick=None):
    """A sleep that advances the fake clock and yields, calling ``on_tick(n)``
    after the n-th sleep so a test can change readings between hold ticks."""
    ticks = []

    async def sleep(seconds):
        clock.now += timedelta(seconds=seconds)
        ticks.append(seconds)
        if on_tick is not None:
            await on_tick(len(ticks))
        await asyncio.sleep(0)

    return sleep, ticks


def _hold_rule(action, conditions=None, rule_id="hold"):
    return {
        "id": rule_id,
        "enabled": True,
        "triggers": [{"type": "event", "event_type": "go"}],
        "conditions": conditions or [],
        "actions": [action],
    }


async def _run_readings(action, entity, readings, conditions=None):
    """Run one hold, feeding ``readings`` to ``entity`` one per tick."""
    clock = Clock()

    async def feed(n):
        if n < len(readings):
            await store.set(entity, readings[n])

    sleep, _ticks = _scripted(clock, feed)
    engine, store, _bus, fake = _build(now=clock, sleep=sleep)
    await store.set(entity, readings[0])
    engine.apply_rules([_hold_rule(action, conditions)])
    engine.start()
    try:
        engine.emit_event("go")
        await engine.join()
    finally:
        await engine.stop()
    return [(name, action) for name, action, _payload in fake.calls]


class TestHoldDecision:
    def test_lower_switches_outside_the_band_and_keeps_state_inside(self):
        assert hold_wants_on("lower", 64, 60, 3, False) is True
        assert hold_wants_on("lower", 62, 60, 3, False) is False
        assert hold_wants_on("lower", 62, 60, 3, True) is True
        assert hold_wants_on("lower", 57, 60, 3, True) is False

    def test_raise_is_the_mirror(self):
        assert hold_wants_on("raise", 20, 22, 1, False) is True
        assert hold_wants_on("raise", 22.5, 22, 1, True) is True
        assert hold_wants_on("raise", 23, 22, 1, True) is False

    def test_missing_reading_fails_safe_to_off(self):
        assert hold_wants_on("raise", None, 22, 1, True) is False


class TestClimateHold:
    async def test_lower_direction_holds_humidity_around_target(self):
        _register("switch.dehumidifier")
        action = {
            "type": "climate_hold",
            "entity": "switch.dehumidifier",
            "sensor": "sensor.rh",
            "target": 60,
            "hysteresis": 3,
            "direction": "lower",
            "min_cycle": 0,
            "seconds": 6 * HOLD_TICK_SECONDS,
        }
        calls = await _run_readings(action, "sensor.rh", [65, 61, 58, 56, 62, 64])
        assert calls == [
            ("dehumidifier", "on"),
            ("dehumidifier", "off"),
            ("dehumidifier", "on"),
            ("dehumidifier", "off"),
        ]

    async def test_raise_direction_heats_until_above_band(self):
        _register("switch.heater")
        action = {
            "type": "climate_hold",
            "entity": "switch.heater",
            "sensor": "sensor.temp",
            "target": 22,
            "hysteresis": 1,
            "direction": "raise",
            "min_cycle": 0,
            "seconds": 4 * HOLD_TICK_SECONDS,
        }
        calls = await _run_readings(action, "sensor.temp", [20, 21.5, 23.5, 22])
        assert calls == [("heater", "on"), ("heater", "off")]

    async def test_min_cycle_stops_chatter(self):
        _register("switch.fan")
        action = {
            "type": "climate_hold",
            "entity": "switch.fan",
            "sensor": "sensor.rh",
            "target": 60,
            "hysteresis": 1,
            "direction": "lower",
            "min_cycle": 60,
            "seconds": 4 * HOLD_TICK_SECONDS,
        }
        calls = await _run_readings(action, "sensor.rh", [62, 58, 62, 58])
        assert calls == [("fan", "on"), ("fan", "off")]

    async def test_vpd_source_uses_the_metric(self):
        _register("switch.humidifier")
        clock = Clock()

        async def feed(n):
            if n == 1:
                await store.set("sensor.rh", 75)

        sleep, _ticks = _scripted(clock, feed)
        engine, store, _bus, fake = _build(now=clock, sleep=sleep)
        await store.set("sensor.temp", 26)
        await store.set("sensor.rh", 50)
        assert vpd_kpa(26, 50) > 1.1 and vpd_kpa(26, 75) < 0.9
        action = {
            "type": "climate_hold",
            "entity": "switch.humidifier",
            "metric": "vpd",
            "temperature": "sensor.temp",
            "humidity": "sensor.rh",
            "target": 1.0,
            "hysteresis": 0.1,
            "direction": "lower",
            "min_cycle": 0,
            "seconds": 2 * HOLD_TICK_SECONDS,
        }
        engine.apply_rules([_hold_rule(action)])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert [c[1] for c in fake.calls] == ["on", "off"]

    async def test_hold_ends_and_switches_off_when_conditions_fail(self):
        _register("light.lamp")
        clock = Clock()
        sleep, ticks = _scripted(clock)
        engine, _store, _bus, fake = _build(now=clock, sleep=sleep)
        window = [{"type": "time", "after": "06:00", "before": "12:00:30"}]
        engine.apply_rules([_hold_rule({"type": "climate_hold", "entity": "light.lamp"}, window)])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert fake.calls == [("lamp", "on", {}), ("lamp", "off", {})]
        assert len(ticks) == 3

    async def test_cancel_switches_off(self):
        _register("light.lamp")
        clock = Clock()

        async def remove_rule(n):
            if n == 3:
                engine.apply_rules([])

        sleep, _ticks = _scripted(clock, remove_rule)
        engine, _store, _bus, fake = _build(now=clock, sleep=sleep)
        engine.apply_rules([_hold_rule({"type": "climate_hold", "entity": "light.lamp"})])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert fake.calls == [("lamp", "on", {}), ("lamp", "off", {})]

    async def test_unchanged_rule_keeps_running_across_republish(self):
        _register("light.lamp")
        clock = Clock()
        rule = _hold_rule({"type": "climate_hold", "entity": "light.lamp"})

        async def republish(n):
            if n == 2:
                engine.apply_rules([json.loads(json.dumps(rule))], stages={"s1": "flowering"})
                engine.emit_event("go")
            if n == 5:
                engine.apply_rules([])

        sleep, ticks = _scripted(clock, republish)
        engine, _store, _bus, fake = _build(now=clock, sleep=sleep)
        engine.apply_rules([rule])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert fake.calls == [("lamp", "on", {}), ("lamp", "off", {})]
        assert len(ticks) == 5

    async def test_changed_rule_run_is_cancelled(self):
        _register("switch.fan")
        engine, _store, _bus, fake = _build(sleep=asyncio.sleep)
        rule = {
            "id": "r",
            "enabled": True,
            "triggers": [{"type": "event", "event_type": "go"}],
            "actions": [
                {"type": "delay", "seconds": 0.05},
                {"type": "call", "entity": "switch.fan", "service": "turn_on"},
            ],
        }
        engine.apply_rules([rule])
        engine.start()
        try:
            engine.emit_event("go")
            await asyncio.sleep(0.01)
            engine.apply_rules([{**rule, "name": "edited"}])
            await engine.join()
            assert fake.calls == []
        finally:
            await engine.stop()


class TestRamp:
    async def test_ramps_in_equal_steps_from_the_start_value(self):
        _register("number.dimmer")
        clock = Clock()
        sleep, ticks = _scripted(clock)
        engine, _store, _bus, fake = _build(now=clock, sleep=sleep)
        ramp = {"type": "ramp", "entity": "number.dimmer", "from": 0, "to": 100}
        engine.apply_rules([_hold_rule({**ramp, "minutes": 1, "steps": 4})])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert [c[2]["value"] for c in fake.calls] == [0, 25, 50, 75, 100]
        assert ticks == [15, 15, 15, 15]

    async def test_defaults_to_current_value_and_one_step_per_minute(self):
        _register("number.dimmer")
        clock = Clock()
        sleep, ticks = _scripted(clock)
        engine, store, _bus, fake = _build(now=clock, sleep=sleep)
        await store.set("number.dimmer", 40)
        ramp = {"type": "ramp", "entity": "number.dimmer", "to": 100, "seconds": 120}
        engine.apply_rules([_hold_rule(ramp)])
        engine.start()
        try:
            engine.emit_event("go")
            await engine.join()
        finally:
            await engine.stop()
        assert [c[2]["value"] for c in fake.calls] == [70, 100]
        assert ticks == [60, 60]


class TestStage:
    def _rule(self, to=None):
        trigger = {"type": "stage", "space": "s1"}
        if to is not None:
            trigger["to"] = to
        return {
            "id": "st",
            "enabled": True,
            "triggers": [trigger],
            "actions": [{"type": "call", "entity": "switch.fan", "service": "turn_on"}],
        }

    async def test_first_apply_seeds_then_a_change_fires(self):
        _register("switch.fan")
        engine, _store, _bus, fake = _build()
        engine.start()
        try:
            engine.apply_rules([self._rule()], stages={"s1": "vegetative"})
            await engine.join()
            assert fake.calls == []
            engine.apply_rules([self._rule()], stages={"s1": "vegetative"})
            await engine.join()
            assert fake.calls == []
            engine.apply_rules([self._rule()], stages={"s1": "flowering"})
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    async def test_to_filters_the_new_stage(self):
        _register("switch.fan")
        engine, _store, _bus, fake = _build()
        engine.start()
        try:
            engine.apply_rules([self._rule("flowering")], stages={"s1": "seedling"})
            engine.apply_rules([self._rule("flowering")], stages={"s1": "vegetative"})
            await engine.join()
            assert fake.calls == []
            engine.apply_rules([self._rule("flowering")], stages={"s1": "flowering"})
            await engine.join()
            assert fake.calls == [("fan", "on", {})]
        finally:
            await engine.stop()

    def test_stage_condition(self):
        engine, _store, _bus, _fake = _build()
        engine.apply_rules([], stages={"s1": "flowering"})
        cond = {"type": "stage", "space": "s1", "stage": "flowering"}
        assert engine._evaluate_condition(cond, FIXED_NOON) is True
        assert engine._evaluate_condition({**cond, "stage": "vegetative"}, FIXED_NOON) is False
        assert engine._evaluate_condition({**cond, "space": "s2"}, FIXED_NOON) is False

    def test_window_ending_at_midnight(self):
        window = {"after": "06:00", "before": "00:00"}
        assert time_condition_matches(window, datetime(2026, 1, 1, 23, 59)) is True
        assert time_condition_matches(window, datetime(2026, 1, 1, 0, 1)) is False
        assert time_condition_matches(window, datetime(2026, 1, 1, 6, 0)) is True

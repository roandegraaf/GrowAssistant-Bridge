"""The bridge-side automation evaluator/executor.

This is the runtime that *runs* the rules the app pushes (pillar P5: the grow
keeps working when the user's internet drops, because evaluation is local). The
``AutomationManager`` owns the rule set and drives this engine — it calls
``apply_rules`` with the enabled rules whenever a newer set is applied.

How it reacts
-------------
* ``state`` / ``numeric_state`` triggers react to ``StateStore`` change
  callbacks. They are **edge-triggered**: a rule fires on the transition *into*
  a match, never on every poll while it holds (otherwise "temp > 30 → fan on"
  re-fires every collection interval forever). The **first** value observed for
  an entity only seeds the baseline and never fires, so a bridge that restarts
  while the tent is already hot does not cause a fan-on storm.
* ``time`` / ``time_pattern`` triggers are evaluated by a ~1s scheduler tick
  against an injectable clock (no croniter — HA's ``/N`` step is hand-rolled).
* ``event`` triggers react to the ``EventBus`` (lifecycle events + ``fire_event``).

Run mode is ``single``: a new trigger firing while a rule's action sequence is
mid-run (e.g. a 600s ``delay``) is ignored. Entities are resolved lazily, so a
rule referencing a device that registers later simply starts working with no
engine rebuild.

Trigger latency ≈ the collection interval (default 60s) for state-based
triggers, because values only refresh when the data-collection loop polls;
brief excursions between samples are missed. This is an accepted, documented
property for a grow tent.
"""

import asyncio
import json
import logging
import math
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import templates
from .event_bus import EventBus
from .executor import ActionExecutor
from .metrics import DliAccumulator, dew_point_c, to_float, vpd_kpa
from .state_store import StateStore

logger = logging.getLogger(__name__)

# A notify publisher delivers a rendered notification intent to the app (over
# MQTT); the ``notification`` action invokes it. Transport-provided at wiring.
NotifyPublisher = Callable[[dict[str, Any]], Awaitable[Any]]

# A fired publisher echoes a completed rule fire to the app (over MQTT, on
# ``…/automations/fired``) so the app can surface "last fired + result" per
# flow. Transport-provided at wiring, like the notify publisher.
FiredPublisher = Callable[[dict[str, Any]], Awaitable[Any]]

# Cap an event chain (fire_event → event trigger → fire_event …) so a bad rule
# cannot spin the bridge. Past this depth events are dropped and logged.
MAX_EVENT_DEPTH = 10

# Fallback cap for a `wait_for_state` action whose `timeout` is omitted. Without
# a bound, asyncio.wait_for(..., None) waits forever; in single-run mode that
# permanently wedges the rule (the run never completes, so its lock never
# releases and every later trigger is rejected). One hour is generous for grow
# automations while still guaranteeing recovery. Tune if longer waits are
# legitimately needed.
DEFAULT_WAIT_FOR_STATE_TIMEOUT = 3600.0

# climate_hold re-reads its sensor this often. Sensor values only refresh every
# collection interval (default 60s), so a faster tick buys nothing.
HOLD_TICK_SECONDS = 15.0
# Minimum time between two switches of a held actuator (compressor/relay wear).
DEFAULT_MIN_CYCLE_SECONDS = 60.0
# A ramp without an explicit step count moves once per minute, capped.
DEFAULT_RAMP_STEP_SECONDS = 60.0
MAX_RAMP_STEPS = 60


class _WaitForStateAborted(Exception):
    """Raised when an *injected* (default) wait_for_state timeout elapses.

    A user-specified timeout means "give up after N seconds and continue" — the
    later actions run. But when the user gave no timeout, the default cap exists
    only to prevent a permanent wedge; if it elapses we must NOT run the
    remaining actions (e.g. "pump off") on a precondition that never actually
    held. This aborts the rest of the rule instead, and releases the run lock.
    """


# Lifecycle event types the bridge seeds onto the bus.
EVENT_BRIDGE_STARTED = "bridge_started"
EVENT_MANIFEST_CHANGED = "manifest_changed"
EVENT_RULE_SET_APPLIED = "rule_set_applied"
EVENT_COMMAND_EXECUTED = "command_executed"


# ─── Pure matching helpers (no engine state — unit-tested directly) ─────────


def parse_time(value: str) -> tuple[int, int, int]:
    """Parse ``HH:MM`` or ``HH:MM:SS`` into an ``(h, m, s)`` tuple."""
    parts = [int(p) for p in value.split(":")]
    if len(parts) == 2:
        return parts[0], parts[1], 0
    return parts[0], parts[1], parts[2]


def numeric_range_match(value: Any, above: Optional[float], below: Optional[float]) -> bool:
    """Whether ``value`` (coerced to float) lies strictly within above/below."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if above is not None and not v > above:
        return False
    if below is not None and not v < below:
        return False
    return True


# Binary-state synonyms, mirroring the app's ``isOn`` display convention
# (``lib/widgets/format.ts``): integrations report on/off states in whatever
# scalar their hardware yields (GPIO ``1``/``0``, MQTT ``"on"``, ESPHome
# ``True``), while the app's flow builder writes the canonical ``"on"``/``"off"``.
_ON_STATES = {"on", "true", "1", "open", "yes"}
_OFF_STATES = {"off", "false", "0", "closed", "no"}


def _canonical_state(value: Any) -> str:
    """Collapse a state scalar to a comparable form.

    Binary synonyms collapse to ``"on"``/``"off"``; numeric strings collapse to
    a canonical float rendering (so ``1.0`` equals ``"1"``); everything else
    compares as its lowercased string.
    """
    s = str(value).strip().lower()
    if s in _ON_STATES:
        return "on"
    if s in _OFF_STATES:
        return "off"
    try:
        f = float(s)
    except ValueError:
        return s
    if f == 1:
        return "on"
    if f == 0:
        return "off"
    return repr(f)


def state_equals(value: Any, target: Any) -> bool:
    """HA-style state comparison, tolerant of binary-state synonyms.

    ``state_equals(1, "on")`` and ``state_equals("Off", False)`` are True; the
    flow builder can therefore always write ``"on"``/``"off"`` regardless of
    which scalar the entity's integration reports.
    """
    return _canonical_state(value) == _canonical_state(target)


def day_interval_matches(trigger: dict[str, Any], now: datetime) -> bool:
    """``every_days`` limits a ``time`` trigger to every Nth day counted from ``starting``."""
    every = trigger.get("every_days")
    if every is None:
        return True
    try:
        start = datetime.strptime(str(trigger.get("starting")), "%Y-%m-%d").date()
    except ValueError:
        start = now.date()
    elapsed = (now.date() - start).days
    return elapsed >= 0 and elapsed % int(every) == 0


def time_trigger_matches(trigger: dict[str, Any], now: datetime) -> bool:
    """Whether a ``time`` trigger (``at: HH:MM[:SS]``) matches ``now``.

    Matches on hour+minute (+second when the literal includes seconds). The
    engine dedupes repeated ticks within the same occurrence, so this only
    needs to answer "is now within the firing instant".
    """
    at = trigger.get("at")
    if not isinstance(at, str):
        return False
    h, m, s = parse_time(at)
    if now.hour != h or now.minute != m:
        return False
    if not day_interval_matches(trigger, now):
        return False
    # A literal with explicit seconds must match the second too; otherwise the
    # trigger fires at the top of the minute.
    return now.second == s if at.count(":") == 2 else now.second == 0


def _resolve_pattern_fields(trigger: dict[str, Any]) -> dict[str, str]:
    """Resolve a ``time_pattern``'s fields, applying HA's defaulting rules.

    Fields *finer* than the finest one provided default to ``"0"`` (so
    ``minutes:'/5'`` fires at second 0, not every second); unspecified fields
    *coarser* than the finest provided default to ``"*"`` (any value).
    """
    order = ["hours", "minutes", "seconds"]
    specs = {f: trigger.get(f) for f in order}
    provided = [i for i, f in enumerate(order) if specs[f] is not None]
    finest = max(provided) if provided else 0
    resolved: dict[str, str] = {}
    for i, f in enumerate(order):
        if specs[f] is not None:
            resolved[f] = str(specs[f])
        elif i > finest:
            resolved[f] = "0"
        else:
            resolved[f] = "*"
    return resolved


def _pattern_field_match(spec: str, value: int) -> bool:
    if spec == "*":
        return True
    if spec.startswith("/"):
        step = int(spec[1:])
        return step > 0 and value % step == 0
    return value == int(spec)


def time_pattern_matches(trigger: dict[str, Any], now: datetime) -> bool:
    """Whether a ``time_pattern`` trigger matches ``now`` (HA ``/N`` semantics)."""
    resolved = _resolve_pattern_fields(trigger)
    return (
        _pattern_field_match(resolved["hours"], now.hour)
        and _pattern_field_match(resolved["minutes"], now.minute)
        and _pattern_field_match(resolved["seconds"], now.second)
    )


def time_condition_matches(condition: dict[str, Any], now: datetime) -> bool:
    """Evaluate a ``time`` condition (``after``/``before``/``weekday``).

    ``weekday`` is HA-style 0=Mon … 6=Sun. An ``after``/``before`` window that
    wraps past midnight (after > before) is supported.
    """
    weekday = condition.get("weekday")
    if weekday is not None and now.weekday() not in weekday:
        return False

    after = condition.get("after")
    before = condition.get("before")
    t = (now.hour, now.minute, now.second)
    a = parse_time(after) if isinstance(after, str) else None
    b = parse_time(before) if isinstance(before, str) else None

    if a is not None and b is not None:
        if a <= b:
            return a <= t <= b
        return t >= a or t <= b  # window wraps midnight
    if a is not None:
        return t >= a
    if b is not None:
        return t <= b
    return True


def hold_wants_on(
    direction: Any, value: Optional[float], target: float, hysteresis: float, is_on: bool
) -> bool:
    """Hysteresis decision for a ``climate_hold`` actuator.

    ``lower``: the actuator pushes the reading down (exhaust, dehumidifier) — on
    at ``target + hysteresis``, off at ``target - hysteresis``. ``raise`` is the
    mirror (heater, humidifier). Inside the band the current state is kept, which
    is what stops it chattering. A missing reading fails safe to off.
    """
    if value is None:
        return False
    high = value >= target + hysteresis
    low = value <= target - hysteresis
    if direction == "lower":
        return True if high else False if low else is_on
    return True if low else False if high else is_on


def duration_seconds(action: dict[str, Any]) -> float:
    """Total seconds for a ``delay`` action's hours/minutes/seconds."""
    return (
        float(action.get("hours", 0) or 0) * 3600
        + float(action.get("minutes", 0) or 0) * 60
        + float(action.get("seconds", 0) or 0)
    )


# ─── Event-chain loop guard ─────────────────────────────────────────────────


class EventChain:
    """Carries depth + a dedupe set across one ``fire_event`` cascade.

    A fresh chain starts each external stimulus (a state/time trigger, or a
    lifecycle/bus event). ``fire_event`` threads the same chain to the rules it
    triggers (incrementing depth, sharing the dedupe set) so an A→B→A loop is
    capped by depth and a repeated identical event within the tick is dropped.
    """

    __slots__ = ("depth", "seen")

    def __init__(self, depth: int = 0, seen: Optional[set] = None) -> None:
        self.depth = depth
        self.seen = seen if seen is not None else set()

    def child(self) -> "EventChain":
        return EventChain(self.depth + 1, self.seen)


# ─── The engine ─────────────────────────────────────────────────────────────


class AutomationEngine:
    """Evaluates triggers/conditions and executes a rule's action sequence."""

    def __init__(
        self,
        state_store: StateStore,
        event_bus: EventBus,
        executor: ActionExecutor,
        now: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], Any] = asyncio.sleep,
        scheduler_interval: float = 1.0,
    ) -> None:
        self._store = state_store
        self._bus = event_bus
        self._executor = executor
        self._notify_publisher: Optional[NotifyPublisher] = None
        self._fired_publisher: Optional[FiredPublisher] = None
        self._now = now
        self._sleep = sleep
        self._scheduler_interval = scheduler_interval

        self._rules: list[dict[str, Any]] = []
        # space id → grow stage, published by the app with the rule set. None
        # until the first apply, which only seeds the baseline for stage triggers.
        self._stages: Optional[dict[str, str]] = None
        # entity_id → list of (rule, trigger) for state/numeric_state triggers
        self._entity_triggers: dict[str, list[tuple[dict, dict]]] = {}
        # previous observed value per entity (for edge detection; absence = unseen)
        self._prev_value: dict[str, Any] = {}
        # rule id → (canonical rule JSON, run task) while its actions run
        # (single run mode). The JSON lets apply_rules keep unchanged runs.
        self._runs: dict[Any, tuple[str, asyncio.Task]] = {}
        # pending `for:` timers, keyed (rule_id, trigger_index)
        self._for_tasks: dict[tuple, asyncio.Task] = {}
        # last-fired marker per time/time_pattern trigger, keyed (rule_id, trigger_index)
        self._time_fired: dict[tuple, Any] = {}
        # outstanding rule-run tasks (so we can await/cancel them)
        self._run_tasks: set[asyncio.Task] = set()
        self._dli: dict[str, DliAccumulator] = {}

        self._started = False
        self._scheduler_task: Optional[asyncio.Task] = None

    def set_notify_publisher(self, fn: NotifyPublisher) -> None:
        """Register the coroutine that delivers a notification intent to the app
        (transport-provided). Invoked by the ``notification`` action; if unset,
        the action logs a warning and continues (same spirit as other optional
        callbacks)."""
        self._notify_publisher = fn

    def set_fired_publisher(self, fn: FiredPublisher) -> None:
        """Register the coroutine that echoes a completed rule fire to the app
        (transport-provided). Called after every fire — i.e. after the action
        sequence ran because a trigger matched and the conditions passed; a
        trigger gated by its conditions is NOT a fire. Optional like the notify
        publisher: unset means no echo, rules run unaffected."""
        self._fired_publisher = fn

    # ─── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        """Begin watching state + events and ticking the time scheduler."""
        if self._started:
            return
        self._started = True
        self._store.subscribe(self._on_state_change)
        self._bus.subscribe(self._on_event)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Automation engine started (%d rule(s))", len(self._rules))
        # Seed the first lifecycle event so the bus is never empty.
        self.emit_event(EVENT_BRIDGE_STARTED, {})

    async def stop(self) -> None:
        """Stop watching, cancel the scheduler and any in-flight runs."""
        self._started = False
        self._store.unsubscribe(self._on_state_change)
        self._bus.unsubscribe(self._on_event)
        self._cancel_tasks()
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        await self.join()
        logger.info("Automation engine stopped")

    def apply_rules(
        self, rules: list[dict[str, Any]], stages: Optional[dict[str, str]] = None
    ) -> None:
        """Replace the running rule set (enabled rules only) and the grow stages.

        Cancels in-flight runs of rules that changed or disappeared (so a deleted
        rule's pending ``delay`` cannot fire), but keeps the run of a rule whose
        definition is byte-for-byte unchanged — every stage change republishes
        the whole set, and that must not interrupt a long ``climate_hold`` or
        ``delay`` elsewhere. Reseeds edge-detection baselines from the
        StateStore's current snapshot (rather than clearing them) — so a
        freshly-applied rule does not fire on the first sample it sees, while
        entities that are *not* being changed keep their real baseline. Resets
        time markers. ``stage`` triggers fire for spaces whose stage differs from
        the previous apply; the first apply only seeds that baseline.
        """
        self._rules = [r for r in rules if isinstance(r, dict)]
        self._cancel_tasks(keep={r.get("id"): _canonical(r) for r in self._rules})
        # Seed from the store so an unrelated rule edit cannot swallow an
        # in-progress transition on some other entity (previously this cleared
        # every baseline, making the next sample look like first_seen).
        self._prev_value = self._store.snapshot()
        self._time_fired.clear()
        self._rebuild_entity_index()
        lights = _dli_light_entities(self._rules)
        self._dli = {e: self._dli.get(e) or DliAccumulator() for e in lights}
        previous_stages = self._stages
        self._stages = {k: v for k, v in (stages or {}).items() if isinstance(v, str)}
        logger.info("Automation engine applied %d enabled rule(s)", len(self._rules))
        if previous_stages is not None:
            self._fire_stage_triggers(previous_stages)

    def _cancel_tasks(self, keep: Optional[dict[Any, str]] = None) -> None:
        for task in list(self._for_tasks.values()):
            task.cancel()
        self._for_tasks.clear()
        for rule_id, (canonical, task) in list(self._runs.items()):
            if keep is not None and keep.get(rule_id) == canonical:
                continue
            task.cancel()
            del self._runs[rule_id]

    async def join(self) -> None:
        """Await all outstanding rule-run tasks, including ones spawned by a
        ``fire_event`` cascade while earlier runs are still awaited. Terminates
        because the event loop guard bounds the cascade."""
        while True:
            tasks = list(self._run_tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)
            self._run_tasks.difference_update(tasks)

    def _rebuild_entity_index(self) -> None:
        self._entity_triggers = {}
        for rule in self._rules:
            for trig in rule.get("triggers") or []:
                if not isinstance(trig, dict):
                    continue
                if trig.get("type") in ("state", "numeric_state"):
                    entity = trig.get("entity")
                    if isinstance(entity, str):
                        self._entity_triggers.setdefault(entity, []).append((rule, trig))

    # ─── State-driven triggers (edge-detected) ──────────────────────

    def _on_state_change(self, entity_id: str, value: Any) -> None:
        """Handle a StateStore change: evaluate state/numeric_state triggers."""
        acc = self._dli.get(entity_id)
        if acc is not None:
            acc.add(value, self._now())
        first_seen = entity_id not in self._prev_value
        old = self._prev_value.get(entity_id)

        for rule, trig in self._entity_triggers.get(entity_id, []):
            try:
                self._evaluate_state_trigger(rule, trig, entity_id, old, value, first_seen)
            except Exception:
                logger.exception("Error evaluating trigger for %s", entity_id)

        self._prev_value[entity_id] = value

    def _evaluate_state_trigger(
        self,
        rule: dict[str, Any],
        trig: dict[str, Any],
        entity_id: str,
        old: Any,
        new: Any,
        first_seen: bool,
    ) -> None:
        key = (rule.get("id"), id(trig))
        ttype = trig.get("type")
        matched = self._state_trigger_fires(ttype, trig, old, new, first_seen)

        # Cancel a pending `for:` timer if the match no longer holds.
        if not self._current_match(ttype, trig, new) and key in self._for_tasks:
            self._for_tasks.pop(key).cancel()

        if not matched:
            return

        for_seconds = trig.get("for")
        if for_seconds:
            self._schedule_for(key, rule, trig, entity_id, for_seconds)
        else:
            self._fire_rule(rule, self._trigger_ctx(trig, new))

    def _state_trigger_fires(
        self, ttype: str, trig: dict[str, Any], old: Any, new: Any, first_seen: bool
    ) -> bool:
        """Edge detection: True only on the transition *into* a match."""
        if first_seen:
            return False  # baseline seed never fires
        if ttype == "numeric_state":
            return numeric_range_match(new, trig.get("above"), trig.get("below")) and not (
                numeric_range_match(old, trig.get("above"), trig.get("below"))
            )
        # state trigger: a change whose new/old states satisfy to/from. The
        # no-change guard is canonical (``1`` → ``"on"`` is not a change), so a
        # post-command write-back that only re-spells the same state never fires.
        to = trig.get("to")
        frm = trig.get("from")
        if state_equals(new, old):
            return False
        if to is not None and not state_equals(new, to):
            return False
        if frm is not None and not state_equals(old, frm):
            return False
        return True

    def _current_match(self, ttype: str, trig: dict[str, Any], value: Any) -> bool:
        """Whether ``value`` currently satisfies the trigger's match (for `for:`)."""
        if ttype == "numeric_state":
            return numeric_range_match(value, trig.get("above"), trig.get("below"))
        to = trig.get("to")
        return to is None or state_equals(value, to)

    def _schedule_for(
        self,
        key: tuple,
        rule: dict[str, Any],
        trig: dict[str, Any],
        entity_id: str,
        for_seconds: float,
    ) -> None:
        """Fire the rule only if the match still holds after ``for`` seconds."""
        if key in self._for_tasks:
            return  # a timer is already pending for this edge

        async def _waiter() -> None:
            try:
                await self._sleep(for_seconds)
                current = self._store.get(entity_id)
                if self._current_match(trig.get("type"), trig, current):
                    self._fire_rule(rule, self._trigger_ctx(trig, current))
            except asyncio.CancelledError:
                pass
            finally:
                self._for_tasks.pop(key, None)

        self._for_tasks[key] = asyncio.create_task(_waiter())

    # ─── Stage triggers ─────────────────────────────────────────────

    def _fire_stage_triggers(self, previous: dict[str, str]) -> None:
        stages = self._stages or {}
        for rule in self._rules:
            for trig in rule.get("triggers") or []:
                if not isinstance(trig, dict) or trig.get("type") != "stage":
                    continue
                stage = stages.get(trig.get("space"))
                if stage is None or stage == previous.get(trig.get("space")):
                    continue
                if trig.get("to") is not None and trig.get("to") != stage:
                    continue
                self._fire_rule(rule, self._trigger_ctx(trig, stage))

    # ─── Time-driven triggers ───────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        # The cadence uses real time (not the injectable action-delay sleep) so a
        # fast test sleep can't turn this into a tight loop. Time triggers are
        # unit-tested by calling _scheduler_tick directly.
        try:
            while self._started:
                try:
                    self._scheduler_tick(self._now())
                except Exception:
                    logger.exception("Error in automation scheduler tick")
                await asyncio.sleep(self._scheduler_interval)
        except asyncio.CancelledError:
            pass

    def _scheduler_tick(self, now: datetime) -> None:
        """Evaluate every time/time_pattern trigger once for clock ``now``."""
        for rule in self._rules:
            for ti, trig in enumerate(rule.get("triggers") or []):
                if not isinstance(trig, dict):
                    continue
                ttype = trig.get("type")
                if ttype == "time":
                    if time_trigger_matches(trig, now):
                        self._fire_time(
                            rule,
                            ti,
                            trig,
                            now,
                            marker=(now.date(), now.hour, now.minute, now.second),
                        )
                elif ttype == "time_pattern":
                    if time_pattern_matches(trig, now):
                        self._fire_time(rule, ti, trig, now, marker=now.replace(microsecond=0))

    def _fire_time(
        self, rule: dict[str, Any], ti: int, trig: dict[str, Any], now: datetime, marker: Any
    ) -> None:
        key = (rule.get("id"), ti)
        if self._time_fired.get(key) == marker:
            return  # already fired for this instant
        self._time_fired[key] = marker
        self._fire_rule(rule, self._trigger_ctx(trig, None))

    # ─── Event-driven triggers ──────────────────────────────────────

    def _on_event(self, event_type: str, event_data: dict[str, Any], meta: Any) -> None:
        """Bus subscriber: fire rules whose ``event`` trigger matches."""
        chain = meta if isinstance(meta, EventChain) else EventChain()
        for rule in self._rules:
            for trig in rule.get("triggers") or []:
                if not isinstance(trig, dict) or trig.get("type") != "event":
                    continue
                if trig.get("event_type") != event_type:
                    continue
                want = trig.get("event_data") or {}
                if all(event_data.get(k) == v for k, v in want.items()):
                    ctx = {"type": "event", "event_type": event_type, "event_data": event_data}
                    self._fire_rule(rule, ctx, chain.child())

    def emit_event(self, event_type: str, event_data: Optional[dict[str, Any]] = None) -> None:
        """Emit an external/lifecycle event (fresh chain). Used by main.py for
        bridge_started / manifest_changed / command_executed (app commands)."""
        self._bus.emit(event_type, event_data or {}, meta=EventChain())

    def _fire_event(self, event_type: str, event_data: dict[str, Any], chain: EventChain) -> None:
        """Emit an event from inside a rule (``fire_event``), guarding loops."""
        if chain.depth >= MAX_EVENT_DEPTH:
            logger.warning("Event chain depth exceeded — dropping '%s'", event_type)
            return
        dedupe_key = (event_type, json.dumps(event_data, sort_keys=True, default=str))
        if dedupe_key in chain.seen:
            logger.warning("Duplicate event '%s' within tick — dropping", event_type)
            return
        chain.seen.add(dedupe_key)
        self._bus.emit(event_type, event_data, meta=chain)

    # ─── Rule execution ─────────────────────────────────────────────

    def _trigger_ctx(self, trig: dict[str, Any], value: Any) -> dict[str, Any]:
        ctx = dict(trig)
        if value is not None:
            ctx["value"] = value
        return ctx

    def _fire_rule(
        self, rule: dict[str, Any], trigger_ctx: dict[str, Any], chain: Optional[EventChain] = None
    ) -> None:
        """Spawn the action sequence, honouring single run mode."""
        rule_id = rule.get("id")
        if rule_id in self._runs:
            logger.info("Rule '%s' already running (single) — ignoring trigger", rule_id)
            return
        task = asyncio.create_task(self._run_rule(rule, trigger_ctx, chain or EventChain()))
        self._runs[rule_id] = (_canonical(rule), task)
        self._run_tasks.add(task)
        task.add_done_callback(self._run_tasks.discard)

    def _release(self, rule_id: Any) -> None:
        if self._runs.get(rule_id, (None, None))[1] is asyncio.current_task():
            del self._runs[rule_id]

    async def _run_rule(
        self, rule: dict[str, Any], trigger_ctx: dict[str, Any], chain: EventChain
    ) -> None:
        rule_id = rule.get("id")
        fired = False
        failures: list[str] = []
        try:
            if not self._evaluate_conditions(rule.get("conditions") or [], self._now()):
                logger.debug("Rule '%s' conditions not met — not running", rule_id)
                return
            fired = True
            variables: dict[str, Any] = {}
            for action in rule.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                failure = await self._run_action(action, trigger_ctx, variables, chain, rule)
                if failure is not None:
                    failures.append(failure)
        except asyncio.CancelledError:
            raise
        except _WaitForStateAborted as abort:
            # Expected control-flow signal, not an error: stop the remaining
            # actions and record why, without a scary traceback.
            logger.info("Rule '%s' aborted: %s", rule_id, abort)
            if fired:
                failures.append(str(abort))
        except Exception as e:
            logger.exception("Error running automation '%s'", rule_id)
            if fired:
                failures.append(f"{type(e).__name__}: {e}")
        finally:
            self._release(rule_id)
        if fired:
            await self._publish_fired(rule_id, failures)

    async def _publish_fired(self, rule_id: Any, failures: list[str]) -> None:
        """Echo a completed fire (`{automationId, ok, error, firedAt}`) to the
        app. Best-effort: a publish failure must never affect rule execution."""
        if self._fired_publisher is None:
            return
        payload = {
            "automationId": rule_id,
            "ok": not failures,
            "error": failures[0] if failures else None,
            "firedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._fired_publisher(payload)
        except Exception:
            logger.exception("Fired echo publish failed for rule '%s'", rule_id)

    async def _run_action(
        self,
        action: dict[str, Any],
        trigger_ctx: dict[str, Any],
        variables: dict[str, Any],
        chain: EventChain,
        rule: dict[str, Any],
    ) -> Optional[str]:
        """Run one action. Returns a short failure description when the action
        ran but did not succeed (currently only a failed/skipped ``call``), or
        None — the sequence continues either way; failures only feed the fired
        echo's result."""
        atype = action.get("type")
        if atype == "call":
            return await self._action_call(action, trigger_ctx, variables, chain)
        elif atype == "delay":
            await self._sleep(duration_seconds(action))
        elif atype == "wait_for_state":
            await self._action_wait_for_state(action)
        elif atype == "set_variable":
            self._action_set_variable(action, trigger_ctx, variables)
        elif atype == "fire_event":
            data = self._render_data(action.get("event_data"), trigger_ctx, variables)
            self._fire_event(action.get("event_type"), data, chain)
        elif atype == "notification":
            await self._action_notification(action, trigger_ctx, variables, rule.get("id"))
        elif atype == "climate_hold":
            return await self._action_climate_hold(action, rule)
        elif atype == "ramp":
            return await self._action_ramp(action)
        else:
            logger.warning("Unknown action type '%s' — skipping", atype)
        return None

    async def _action_call(
        self,
        action: dict[str, Any],
        trigger_ctx: dict[str, Any],
        variables: dict[str, Any],
        chain: EventChain,
    ) -> Optional[str]:
        entity = action.get("entity")
        service = action.get("service")
        data = self._render_data(action.get("data"), trigger_ctx, variables)
        ok = await self._executor.call(entity, service, data)
        # command_executed flows through the same chain so a call→event→call
        # cascade is depth/dedupe guarded like any fire_event.
        self._fire_event(
            EVENT_COMMAND_EXECUTED,
            {"entity": entity, "service": service, "success": bool(ok)},
            chain,
        )
        # A False call (entity not registered, integration missing, or the
        # command itself failed) is reported in the fired echo, matching what
        # the executor already logged.
        return None if ok else f"call '{service}' on '{entity}' failed"

    async def _action_climate_hold(
        self, action: dict[str, Any], rule: dict[str, Any]
    ) -> Optional[str]:
        """Keep an actuator switching around a target until the hold ends.

        The hold ends when its duration elapses, when the rule's conditions stop
        passing (e.g. a time window closes or the stage changes), or when the run
        is cancelled (rule edited/removed, bridge stopping). It owns the actuator:
        on every exit it is switched off. Without a sensor or metric the actuator
        is simply held on (a light schedule). The actuator state is re-read from
        the store every tick instead of remembered, so an off sent by a cancelled
        predecessor is noticed and corrected.
        """
        entity = action.get("entity")
        total = duration_seconds(action)
        deadline = self._now() + timedelta(seconds=total) if total > 0 else None
        min_cycle = to_float(action.get("min_cycle"))
        min_cycle = DEFAULT_MIN_CYCLE_SECONDS if min_cycle is None else min_cycle
        sensorless = action.get("sensor") is None and action.get("metric") is None
        also = [] if sensorless else list(action.get("also") or [])
        # With extra readings each keeps its own latch, so one reading inside
        # its band can't hold the actuator on after the reading that switched
        # it on has recovered.
        readings = [action, *also]
        demands = [False] * len(readings)
        last_switch: Optional[datetime] = None
        failure: Optional[str] = None
        try:
            while True:
                now = self._now()
                if deadline is not None and now >= deadline:
                    break
                if not self._evaluate_conditions(rule.get("conditions") or [], now):
                    break
                is_on = state_equals(self._store.get(entity), "on")
                if sensorless:
                    want = True
                elif not also:
                    want = self._hold_demand(action, is_on)
                else:
                    demands = [self._hold_demand(r, d) for r, d in zip(readings, demands)]
                    want = any(demands)
                cooled = last_switch is None or (now - last_switch).total_seconds() >= min_cycle
                if want != is_on and cooled:
                    service = "turn_on" if want else "turn_off"
                    if await self._executor.call(entity, service, {}):
                        last_switch = now
                    else:
                        failure = f"climate_hold '{service}' on '{entity}' failed"
                await self._sleep(HOLD_TICK_SECONDS)
        finally:
            if state_equals(self._store.get(entity), "on"):
                await self._executor.call(entity, "turn_off", {})
        return failure

    def _hold_demand(self, reading: dict[str, Any], latched: bool) -> bool:
        return hold_wants_on(
            reading.get("direction"),
            self._hold_reading(reading),
            to_float(reading.get("target")) or 0.0,
            to_float(reading.get("hysteresis")) or 0.0,
            latched,
        )

    def _hold_reading(self, action: dict[str, Any]) -> Optional[float]:
        if action.get("metric") == "vpd":
            temp = to_float(self._store.get(action.get("temperature")))
            rh = to_float(self._store.get(action.get("humidity")))
            if temp is None or rh is None:
                return None
            offset = to_float(action.get("leaf_offset"))
            return vpd_kpa(temp, rh, -2.0 if offset is None else offset)
        return to_float(self._store.get(action.get("sensor")))

    async def _action_ramp(self, action: dict[str, Any]) -> Optional[str]:
        """Move a dimmable entity from ``from`` (else its current value, else 0)
        to ``to`` in equal ``set_value`` steps spread over the duration."""
        entity = action.get("entity")
        target = to_float(action.get("to"))
        if target is None:
            return f"ramp on '{entity}' has no target"
        start = to_float(action.get("from"))
        if start is None:
            start = to_float(self._store.get(entity))
        if start is None:
            start = 0.0
        total = duration_seconds(action)
        steps = int(to_float(action.get("steps")) or 0) or max(
            1, min(MAX_RAMP_STEPS, math.ceil(total / DEFAULT_RAMP_STEP_SECONDS))
        )
        ok = True
        if action.get("from") is not None:
            ok = await self._executor.call(entity, "set_value", {"value": start}) and ok
        for i in range(1, steps + 1):
            if total > 0:
                await self._sleep(total / steps)
            value = round(start + (target - start) * i / steps, 2)
            ok = await self._executor.call(entity, "set_value", {"value": value}) and ok
        return None if ok else f"ramp on '{entity}' failed"

    async def _action_wait_for_state(self, action: dict[str, Any]) -> None:
        entity = action.get("entity")
        state = action.get("state")
        above = action.get("above")
        below = action.get("below")
        user_timeout = action.get("timeout")
        # An omitted (or non-positive) timeout would wait forever and, in
        # single-run mode, permanently wedge the rule. Fall back to a bounded
        # default so the run always completes; but distinguish it from a
        # user-specified timeout so we can abort (not continue) if it elapses.
        injected = not user_timeout or user_timeout <= 0
        timeout = DEFAULT_WAIT_FOR_STATE_TIMEOUT if injected else user_timeout

        def predicate() -> bool:
            cur = self._store.get(entity)
            if cur is None:
                return False
            if state is not None:
                return state_equals(cur, state)
            return numeric_range_match(cur, above, below)

        ok = await self._store.wait_for(predicate, timeout)
        if not ok:
            if injected:
                # The user did not ask to give up — don't run downstream actions
                # on a condition that never held. Abort the rule instead.
                raise _WaitForStateAborted(
                    f"wait_for_state on '{entity}' not satisfied within the "
                    f"default {DEFAULT_WAIT_FOR_STATE_TIMEOUT:.0f}s cap"
                )
            logger.info("wait_for_state on '%s' timed out — continuing", entity)

    def _action_set_variable(
        self, action: dict[str, Any], trigger_ctx: dict[str, Any], variables: dict[str, Any]
    ) -> None:
        name = action.get("name")
        if "value_template" in action and action["value_template"] is not None:
            source = action["value_template"]
        else:
            source = action.get("value")
        try:
            variables[name] = templates.render(
                source,
                variables=variables,
                trigger=trigger_ctx,
                states=self._store.snapshot(),
            )
        except templates.TemplateError as e:
            logger.warning("set_variable '%s' template failed: %s", name, e)
            variables[name] = None

    async def _action_notification(
        self,
        action: dict[str, Any],
        trigger_ctx: dict[str, Any],
        variables: dict[str, Any],
        rule_id: Any,
    ) -> None:
        """Render the title/message templates and hand the notification intent to
        the app over MQTT (the app fans it out as Web Push). No-op with a warning
        if no publisher is wired — like the other optional callbacks."""
        title = self._render_str(action.get("title"), trigger_ctx, variables)
        message = self._render_str(action.get("message"), trigger_ctx, variables)
        if self._notify_publisher is None:
            logger.warning(
                "notification fired for rule '%s' but no notify publisher wired", rule_id
            )
            return
        payload = {
            "automationId": rule_id,
            "title": title,
            "message": message,
            "firedAt": datetime.now(timezone.utc).isoformat(),
        }
        await self._notify_publisher(payload)

    def _render_data(
        self, data: Optional[dict[str, Any]], trigger_ctx: dict[str, Any], variables: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return templates.render_data(
                data or {},
                variables=variables,
                trigger=trigger_ctx,
                states=self._store.snapshot(),
            )
        except templates.TemplateError as e:
            logger.warning("payload template failed: %s — using raw values", e)
            return dict(data or {})

    def _render_str(
        self, value: Any, trigger_ctx: dict[str, Any], variables: dict[str, Any]
    ) -> Any:
        """Render ``{{ … }}`` templates in a single string field (title/message),
        falling back to the raw value if a template fails — mirrors
        ``_render_data``'s handling for payload dicts."""
        try:
            return templates.render(
                value,
                variables=variables,
                trigger=trigger_ctx,
                states=self._store.snapshot(),
            )
        except templates.TemplateError as e:
            logger.warning("notification template failed: %s — using raw value", e)
            return value

    # ─── Conditions ─────────────────────────────────────────────────

    def _evaluate_conditions(self, conditions: list[dict[str, Any]], now: datetime) -> bool:
        """All top-level conditions must hold (implicit AND)."""
        return all(self._evaluate_condition(c, now) for c in conditions if isinstance(c, dict))

    def _evaluate_condition(self, condition: dict[str, Any], now: datetime) -> bool:
        ctype = condition.get("type")
        if ctype == "and":
            subs = condition.get("conditions") or []
            return all(self._evaluate_condition(c, now) for c in subs if isinstance(c, dict))
        if ctype == "or":
            subs = condition.get("conditions") or []
            return any(self._evaluate_condition(c, now) for c in subs if isinstance(c, dict))
        if ctype == "not":
            subs = condition.get("conditions") or []
            return not any(self._evaluate_condition(c, now) for c in subs if isinstance(c, dict))
        if ctype == "state":
            cur = self._store.get(condition.get("entity"))
            return cur is not None and state_equals(cur, condition.get("state"))
        if ctype == "numeric_state":
            cur = self._store.get(condition.get("entity"))
            return cur is not None and numeric_range_match(
                cur, condition.get("above"), condition.get("below")
            )
        if ctype == "time":
            return time_condition_matches(condition, now)
        if ctype == "stage":
            stage = (self._stages or {}).get(condition.get("space"))
            return stage is not None and stage == condition.get("stage")
        if ctype == "derived":
            value = self._derived_value(condition, now)
            return value is not None and numeric_range_match(
                value, condition.get("above"), condition.get("below")
            )
        logger.warning("Unknown condition type '%s' — treating as false", ctype)
        return False

    def _derived_value(self, condition: dict[str, Any], now: datetime) -> Optional[float]:
        metric = condition.get("metric")
        if metric == "dli":
            acc = self._dli.get(condition.get("light"))
            return acc.value(now) if acc is not None else None
        temp = to_float(self._store.get(condition.get("temperature")))
        rh = to_float(self._store.get(condition.get("humidity")))
        if temp is None or rh is None:
            return None
        if metric == "vpd":
            offset = to_float(condition.get("leaf_offset"))
            return vpd_kpa(temp, rh, -2.0 if offset is None else offset)
        if metric == "dew_point":
            return dew_point_c(temp, rh)
        return None


def _canonical(rule: dict[str, Any]) -> str:
    return json.dumps(rule, sort_keys=True, default=str)


def _dli_light_entities(rules: list[dict[str, Any]]) -> set[str]:
    lights: set[str] = set()
    stack = [c for r in rules for c in (r.get("conditions") or [])]
    while stack:
        c = stack.pop()
        if not isinstance(c, dict):
            continue
        stack.extend(c.get("conditions") or [])
        if c.get("type") == "derived" and c.get("metric") == "dli":
            if isinstance(c.get("light"), str):
                lights.add(c["light"])
    return lights

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
from datetime import datetime
from typing import Any, Callable, Optional

from . import templates
from .event_bus import EventBus
from .executor import ActionExecutor
from .state_store import StateStore

logger = logging.getLogger(__name__)

# Cap an event chain (fire_event → event trigger → fire_event …) so a bad rule
# cannot spin the bridge. Past this depth events are dropped and logged.
MAX_EVENT_DEPTH = 10

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


def state_equals(value: Any, target: Any) -> bool:
    """HA-style state comparison: stringified value equals stringified target."""
    return str(value) == str(target)


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
        self._now = now
        self._sleep = sleep
        self._scheduler_interval = scheduler_interval

        self._rules: list[dict[str, Any]] = []
        # entity_id → list of (rule, trigger) for state/numeric_state triggers
        self._entity_triggers: dict[str, list[tuple[dict, dict]]] = {}
        # previous observed value per entity (for edge detection; absence = unseen)
        self._prev_value: dict[str, Any] = {}
        # rule ids with an action sequence currently running (single run mode)
        self._active: set[str] = set()
        # pending `for:` timers, keyed (rule_id, trigger_index)
        self._for_tasks: dict[tuple, asyncio.Task] = {}
        # last-fired marker per time/time_pattern trigger, keyed (rule_id, trigger_index)
        self._time_fired: dict[tuple, Any] = {}
        # outstanding rule-run tasks (so we can await/cancel them)
        self._run_tasks: set[asyncio.Task] = set()

        self._started = False
        self._scheduler_task: Optional[asyncio.Task] = None

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

    def apply_rules(self, rules: list[dict[str, Any]]) -> None:
        """Replace the running rule set (enabled rules only).

        Cancels in-flight runs/timers and resets edge-detection baselines and
        time markers — so a freshly-applied rule does not fire on the first
        sample it sees, and a deleted rule's pending ``delay`` cannot fire.
        Idempotent: re-applying the same set is safe (the manager only calls
        this for a strictly-newer version, so reconnect redelivery never resets
        baselines).
        """
        self._cancel_tasks()
        self._rules = [r for r in rules if isinstance(r, dict)]
        self._prev_value.clear()
        self._time_fired.clear()
        self._rebuild_entity_index()
        logger.info("Automation engine applied %d enabled rule(s)", len(self._rules))

    def _cancel_tasks(self) -> None:
        for task in list(self._for_tasks.values()):
            task.cancel()
        self._for_tasks.clear()
        for task in list(self._run_tasks):
            task.cancel()
        self._active.clear()

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
        # state trigger: a change whose new/old states satisfy to/from
        to = trig.get("to")
        frm = trig.get("from")
        if new == old:
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
        if rule_id in self._active:
            logger.info("Rule '%s' already running (single) — ignoring trigger", rule_id)
            return
        self._active.add(rule_id)
        task = asyncio.create_task(self._run_rule(rule, trigger_ctx, chain or EventChain()))
        self._run_tasks.add(task)
        task.add_done_callback(self._run_tasks.discard)

    async def _run_rule(
        self, rule: dict[str, Any], trigger_ctx: dict[str, Any], chain: EventChain
    ) -> None:
        rule_id = rule.get("id")
        try:
            if not self._evaluate_conditions(rule.get("conditions") or [], self._now()):
                logger.debug("Rule '%s' conditions not met — not running", rule_id)
                return
            variables: dict[str, Any] = {}
            for action in rule.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                await self._run_action(action, trigger_ctx, variables, chain)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error running automation '%s'", rule_id)
        finally:
            self._active.discard(rule_id)

    async def _run_action(
        self,
        action: dict[str, Any],
        trigger_ctx: dict[str, Any],
        variables: dict[str, Any],
        chain: EventChain,
    ) -> None:
        atype = action.get("type")
        if atype == "call":
            await self._action_call(action, trigger_ctx, variables, chain)
        elif atype == "delay":
            await self._sleep(duration_seconds(action))
        elif atype == "wait_for_state":
            await self._action_wait_for_state(action)
        elif atype == "set_variable":
            self._action_set_variable(action, trigger_ctx, variables)
        elif atype == "fire_event":
            data = self._render_data(action.get("event_data"), trigger_ctx, variables)
            self._fire_event(action.get("event_type"), data, chain)
        else:
            logger.warning("Unknown action type '%s' — skipping", atype)

    async def _action_call(
        self,
        action: dict[str, Any],
        trigger_ctx: dict[str, Any],
        variables: dict[str, Any],
        chain: EventChain,
    ) -> None:
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

    async def _action_wait_for_state(self, action: dict[str, Any]) -> None:
        entity = action.get("entity")
        state = action.get("state")
        above = action.get("above")
        below = action.get("below")
        timeout = action.get("timeout")

        def predicate() -> bool:
            cur = self._store.get(entity)
            if cur is None:
                return False
            if state is not None:
                return state_equals(cur, state)
            return numeric_range_match(cur, above, below)

        ok = await self._store.wait_for(predicate, timeout)
        if not ok:
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
        logger.warning("Unknown condition type '%s' — treating as false", ctype)
        return False

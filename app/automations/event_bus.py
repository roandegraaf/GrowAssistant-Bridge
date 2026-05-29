"""A general in-process event bus for the automation engine.

``event`` triggers subscribe to it, ``fire_event`` actions publish to it, and
the bridge seeds real lifecycle events (``bridge_started``, ``manifest_changed``,
``rule_set_applied``, ``command_executed``) so rules can react to bridge
activity even before any rule fires an event of its own.

Delivery is synchronous-to-the-loop: ``emit`` invokes each subscriber with
``(event_type, event_data, meta)``. ``meta`` is opaque to the bus — the engine
uses it to carry the event-chain context (depth + dedupe set) through a
``fire_event`` cascade so a runaway rule cannot spin the bridge (see
``engine.EventChain``). Subscribers are expected to be cheap (the engine's
subscriber only schedules rule runs); an exception in one subscriber never
stops the others.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# A subscriber receives (event_type, event_data, meta).
EventSubscriber = Callable[[str, dict[str, Any], Any], None]


class EventBus:
    """Minimal synchronous pub/sub keyed by arbitrary event-type strings."""

    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, cb: EventSubscriber) -> None:
        """Register a subscriber invoked on every ``emit``."""
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: EventSubscriber) -> None:
        """Deregister a subscriber. No-op if absent."""
        try:
            self._subscribers.remove(cb)
        except ValueError:
            pass

    def emit(
        self,
        event_type: str,
        event_data: Optional[dict[str, Any]] = None,
        meta: Any = None,
    ) -> None:
        """Fan an event out to every subscriber.

        ``meta`` is passed through verbatim; external/lifecycle emitters leave
        it None (a fresh chain) while ``fire_event`` threads the running rule's
        chain so the loop guard spans the whole cascade.
        """
        data = event_data or {}
        for cb in list(self._subscribers):
            try:
                cb(event_type, data, meta)
            except Exception:
                logger.exception("EventBus subscriber raised for event '%s'", event_type)

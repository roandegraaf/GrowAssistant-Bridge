"""Change-notifying store of the latest value per entity.

The evaluator's ``state``/``numeric_state`` triggers and the ``wait_for_state``
action both need to *react* to device-value changes, not poll. A plain dict
cannot be awaited, so the store pairs its data with an ``asyncio.Condition`` for
``wait_for_state`` (which suspends until a predicate holds or a timeout elapses)
and a list of change callbacks for the trigger watcher (invoked synchronously
on the loop after every ``set``). One primitive serves both reactive paths.

Value convention
----------------
Values are stored verbatim as the data-collection loop reports them
(``item["value"]``): a number for sensors (e.g. ``21.7``) and ``1``/``0`` for
gpio/switch inputs. ``state`` comparisons therefore run against ``str(value)``
— a rule that wants "switch is on" matches ``to: "1"``. Actuators are not polled
(GPIO ``receive_data`` yields input pins only), so the executor optimistically
writes the commanded state back here after a ``call`` (``on`` → ``1``,
``off`` → ``0``) so switch state-triggers and ``wait_for_state`` on an actuator
still work.
"""

import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# A change callback receives (entity_id, value) on the asyncio loop after a set.
ChangeCallback = Callable[[str, Any], None]


class StateStore:
    """Latest value per entity with awaitable change-notification."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._cond = asyncio.Condition()
        self._callbacks: list[ChangeCallback] = []

    # ─── Reads ──────────────────────────────────────────────────────

    def get(self, entity_id: str) -> Any:
        """Return the latest value for ``entity_id`` (None if never seen)."""
        return self._values.get(entity_id)

    def has(self, entity_id: str) -> bool:
        """Whether a value has ever been recorded for ``entity_id``."""
        return entity_id in self._values

    def snapshot(self) -> dict[str, Any]:
        """A shallow copy of all current values (for the template context)."""
        return dict(self._values)

    # ─── Writes ─────────────────────────────────────────────────────

    async def set(self, entity_id: str, value: Any) -> None:
        """Record a new value and wake everything waiting on a change.

        Notifies ``wait_for`` waiters under the condition lock, then fires the
        change callbacks (outside the lock, to avoid reentrancy if a callback
        schedules work that reads the store).
        """
        async with self._cond:
            self._values[entity_id] = value
            self._cond.notify_all()
        for cb in list(self._callbacks):
            try:
                cb(entity_id, value)
            except Exception:
                logger.exception("StateStore change callback raised for %s", entity_id)

    # ─── Change subscription (trigger watcher) ──────────────────────

    def subscribe(self, cb: ChangeCallback) -> None:
        """Register a callback fired (sync, on the loop) after every ``set``."""
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def unsubscribe(self, cb: ChangeCallback) -> None:
        """Deregister a change callback. No-op if absent."""
        try:
            self._callbacks.remove(cb)
        except ValueError:
            pass

    # ─── Awaitable predicate (wait_for_state) ───────────────────────

    async def wait_for(
        self, predicate: Callable[[], bool], timeout: Optional[float] = None
    ) -> bool:
        """Await until ``predicate()`` holds, or ``timeout`` seconds elapse.

        Returns True if the predicate became (or already was) true, False on
        timeout. Checks the current state first so an already-satisfied wait
        returns immediately without suspending.
        """
        if predicate():
            return True

        async def _waiter() -> None:
            async with self._cond:
                await self._cond.wait_for(predicate)

        try:
            await asyncio.wait_for(_waiter(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

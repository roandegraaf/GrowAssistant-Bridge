"""Tests for the change-notifying StateStore.

The store is the reactive primitive behind state/numeric_state triggers and the
``wait_for_state`` action — so the load-bearing behaviour is that ``wait_for``
resolves when a matching value arrives, returns immediately when already
matching, times out otherwise, and that change callbacks fire on every set.
"""

import asyncio

from app.automations.state_store import StateStore


class TestReadsWrites:
    async def test_get_and_has(self):
        store = StateStore()
        assert store.get("sensor.temp") is None
        assert store.has("sensor.temp") is False
        await store.set("sensor.temp", 21.5)
        assert store.get("sensor.temp") == 21.5
        assert store.has("sensor.temp") is True

    async def test_snapshot_is_a_copy(self):
        store = StateStore()
        await store.set("a", 1)
        snap = store.snapshot()
        snap["a"] = 999
        assert store.get("a") == 1


class TestChangeCallbacks:
    async def test_callback_fires_on_every_set(self):
        store = StateStore()
        seen = []
        store.subscribe(lambda eid, val: seen.append((eid, val)))
        await store.set("sensor.temp", 20)
        await store.set("sensor.temp", 21)
        assert seen == [("sensor.temp", 20), ("sensor.temp", 21)]

    async def test_unsubscribe_stops_callbacks(self):
        store = StateStore()
        seen = []
        cb = lambda eid, val: seen.append(eid)  # noqa: E731
        store.subscribe(cb)
        await store.set("a", 1)
        store.unsubscribe(cb)
        await store.set("a", 2)
        assert seen == ["a"]

    async def test_one_raising_callback_does_not_break_others(self):
        store = StateStore()
        seen = []

        def bad(eid, val):
            raise RuntimeError("boom")

        store.subscribe(bad)
        store.subscribe(lambda eid, val: seen.append(eid))
        await store.set("a", 1)
        assert seen == ["a"]


class TestWaitFor:
    async def test_returns_immediately_when_already_matching(self):
        store = StateStore()
        await store.set("sensor.ready", "1")
        assert await store.wait_for(lambda: store.get("sensor.ready") == "1", timeout=1) is True

    async def test_resolves_when_a_matching_value_arrives(self):
        store = StateStore()

        async def producer():
            await asyncio.sleep(0.01)
            await store.set("sensor.ready", "1")

        asyncio.create_task(producer())
        ok = await store.wait_for(lambda: store.get("sensor.ready") == "1", timeout=1)
        assert ok is True

    async def test_times_out_when_never_matches(self):
        store = StateStore()
        ok = await store.wait_for(lambda: store.get("sensor.ready") == "1", timeout=0.02)
        assert ok is False

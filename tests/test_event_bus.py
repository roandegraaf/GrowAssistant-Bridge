"""Tests for the general EventBus (subscribe / emit / meta pass-through)."""

from app.automations.event_bus import EventBus


class TestEventBus:
    def test_emit_delivers_type_data_and_meta(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda t, d, m: seen.append((t, d, m)))
        bus.emit("ping", {"x": 1}, meta="chain")
        assert seen == [("ping", {"x": 1}, "chain")]

    def test_emit_defaults_data_to_empty_dict(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda t, d, m: seen.append((t, d, m)))
        bus.emit("ping")
        assert seen == [("ping", {}, None)]

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe(lambda t, d, m: a.append(t))
        bus.subscribe(lambda t, d, m: b.append(t))
        bus.emit("go")
        assert a == ["go"] and b == ["go"]

    def test_unsubscribe(self):
        bus = EventBus()
        seen = []
        cb = lambda t, d, m: seen.append(t)  # noqa: E731
        bus.subscribe(cb)
        bus.emit("a")
        bus.unsubscribe(cb)
        bus.emit("b")
        assert seen == ["a"]

    def test_one_raising_subscriber_does_not_break_others(self):
        bus = EventBus()
        seen = []

        def bad(t, d, m):
            raise RuntimeError("boom")

        bus.subscribe(bad)
        bus.subscribe(lambda t, d, m: seen.append(t))
        bus.emit("go")
        assert seen == ["go"]

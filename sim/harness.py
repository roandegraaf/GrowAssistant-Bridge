"""Run the bridge's real automation stack on a virtual clock, for the grow simulator.

The app's grow simulator (``npm run sim`` in GrowAssistant) spawns this module
and talks to it over stdio, one JSON object per line in each direction. It
stands in for the hardware and the MQTT broker only: the registry, manifest,
calibration, AutomationManager, AutomationEngine, ActionExecutor and command
handling are the production code paths.

Time is virtual. The asyncio loop reports the simulator's clock from
``time()``, so every ``asyncio.sleep``, hold tick, ``delay`` and ``for:`` timer
runs on simulated time, and the engine's scheduler ticks once per simulated
minute (time triggers match at second 0 of a minute).

Requests (``op``): ``init``, ``advance``, ``rules``, ``command``, ``fault``,
``manifest``. Each reply is ``{"ok": true, "events": [...], ...}`` or
``{"ok": false, "error": "..."}``. Events carry what the real bridge would
publish (``status``, ``fired``, ``notify``) plus ``device`` events: an actuator
was switched, which the simulator applies to its physical model.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app import registry as registry_module
from app.automations import (
    ActionExecutor,
    AutomationEngine,
    AutomationManager,
    EventBus,
    StateStore,
)
from app.automations import engine as engine_module
from app.automations import manager as manager_module
from app.calibration import calibrate
from app.commands import run_command
from app.entity_id import derive_domain
from app.integrations import Integration
from app.registry import DeviceCategory, registry

SCHEDULER_INTERVAL_SECONDS = 60.0
SETTLE_LIMIT = 100_000


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock only moves when the simulator moves it."""

    def __init__(self, start: float) -> None:
        super().__init__()
        self.vt = start
        # Epoch-scale floats can't resolve asyncio's 1 ns default, which would
        # leave a timer due exactly now stuck in the heap forever.
        self._clock_resolution = 1e-3

    def time(self) -> float:
        return self.vt


_loop: Optional[VirtualClockLoop] = None


def _clock() -> float:
    return _loop.vt if _loop is not None else time.time()


class _VirtualDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime.fromtimestamp(_clock(), tz)


for _module in (engine_module, manager_module, registry_module):
    _module.datetime = _VirtualDatetime  # type: ignore[attr-defined]


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class SimIntegration(Integration):
    """The simulated hardware: devices the simulator describes, values it supplies."""

    def __init__(self, config: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(config)
        self._emit = emit
        self.failing: set[str] = set()

    async def connect(self) -> bool:
        return True

    def register_capabilities(self, reg: Any) -> None:
        for device in self.config.get("devices", []):
            category = (
                DeviceCategory.ACTUATOR
                if device.get("category") == "actuator"
                else DeviceCategory.SENSOR
            )
            reg.register_device(
                name=device["name"],
                domain=derive_domain(self.name),
                device_type=device.get("deviceType") or device["name"],
                category=category,
                integration_name=self.name,
                capabilities=device.get("capabilities") or [],
                metadata=device.get("metadata") or {},
            )

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        if target_id in self.failing:
            return False
        self._emit(
            {
                "kind": "device",
                "name": target_id,
                "action": action,
                "payload": payload,
                "atMs": int(_clock() * 1000),
            }
        )
        return True

    async def send_data(self, data: dict[str, Any]) -> bool:
        return True

    async def receive_data(self):  # pragma: no cover - the harness feeds samples directly
        if False:
            yield {}

    async def get_device_data(self) -> dict[str, Any]:
        return {}


class Harness:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.integration: Optional[SimIntegration] = None
        self.manager: Optional[AutomationManager] = None
        self.engine: Optional[AutomationEngine] = None
        self.store: Optional[StateStore] = None
        self.manifest_version = 0

    @property
    def loop(self) -> VirtualClockLoop:
        assert _loop is not None
        return _loop

    def _integrations(self) -> dict[str, Integration]:
        return {self.integration.name: self.integration} if self.integration else {}

    async def _publish(self, kind: str, payload: dict[str, Any]) -> bool:
        self.events.append({"kind": kind, "payload": payload, "atMs": int(_clock() * 1000)})
        return True

    async def _settle(self) -> None:
        loop = self.loop
        quiet = 0
        for _ in range(SETTLE_LIMIT):
            await asyncio.sleep(0)
            due = any(not h._cancelled and h.when() <= loop.vt for h in loop._scheduled)
            if not loop._ready and not due:
                quiet += 1
                if quiet >= 2:
                    return
            else:
                quiet = 0
        raise RuntimeError("automation engine did not settle (runaway loop?)")

    async def _advance_to(self, target: float) -> None:
        loop = self.loop
        await self._settle()
        while True:
            pending = [h.when() for h in loop._scheduled if not h._cancelled]
            nxt = min(pending) if pending else None
            if nxt is None or nxt > target:
                break
            loop.vt = max(loop.vt, nxt)
            await self._settle()
        loop.vt = max(loop.vt, target)
        await self._settle()

    def _manifest(self) -> dict[str, Any]:
        self.manifest_version += 1
        return {
            "manifest": registry.serialize_manifest(self.manifest_version),
            "state": {
                "online": True,
                "manifestHash": registry.compute_manifest_hash(),
                "manifestVersion": self.manifest_version,
            },
        }

    async def op_init(self, req: dict[str, Any]) -> dict[str, Any]:
        registry.clear()
        self.integration = SimIntegration({"devices": req.get("devices", [])}, self.events.append)
        self.integration.register_capabilities(registry)

        self.store = StateStore()
        bus = EventBus()
        executor = ActionExecutor(
            integration_provider=lambda name: self._integrations().get(name),
            state_store=self.store,
        )
        self.engine = AutomationEngine(
            self.store, bus, executor, now=self._now, scheduler_interval=SCHEDULER_INTERVAL_SECONDS
        )
        self.manager = AutomationManager()
        self.manager.set_status_publisher(lambda status: self._publish("status", status))
        self.manager.set_engine(self.engine)
        self.engine.set_notify_publisher(lambda n: self._publish("notify", n))
        self.engine.set_fired_publisher(lambda f: self._publish("fired", f))
        self.manager.start_engine()
        await self._settle()
        return self._manifest()

    @staticmethod
    def _now() -> datetime:
        return datetime.fromtimestamp(_clock())

    async def op_manifest(self, req: dict[str, Any]) -> dict[str, Any]:
        return self._manifest()

    async def op_advance(self, req: dict[str, Any]) -> dict[str, Any]:
        await self._advance_to(req["toMs"] / 1000)
        telemetry = []
        assert self.integration is not None and self.manager is not None and self.store
        for sample in req.get("samples") or []:
            point = self.integration.telemetry_sample(sample["name"], sample["value"])
            entity_id = point["entity_id"]
            value = calibrate(point["value"], self.manager.calibrations.get(entity_id))
            await self.store.set(entity_id, value)
            telemetry.append({"entityId": entity_id, "value": value, "ts": _iso_utc(_clock())})
        await self._settle()
        return {"telemetry": telemetry}

    async def op_rules(self, req: dict[str, Any]) -> dict[str, Any]:
        assert self.manager is not None
        await self.manager.apply_payload(req.get("payload", "").encode("utf-8"))
        await self._settle()
        return {}

    async def op_command(self, req: dict[str, Any]) -> dict[str, Any]:
        command = req["command"]
        success, message = await run_command(
            command, registry=registry, integrations=self._integrations(), engine=self.engine
        )
        await self._settle()
        return {"ack": {"id": command.get("id"), "success": success, "message": message}}

    async def op_fault(self, req: dict[str, Any]) -> dict[str, Any]:
        assert self.integration is not None
        if req.get("failing"):
            self.integration.failing.add(req["name"])
        else:
            self.integration.failing.discard(req["name"])
        return {}

    async def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"op_{req.get('op')}", None)
        if handler is None:
            raise ValueError(f"unknown op: {req.get('op')}")
        result = await handler(req)
        events = list(self.events)
        self.events.clear()
        return {"ok": True, "events": events, **result}


def main() -> None:
    global _loop
    logging.basicConfig(level=os.environ.get("SIM_BRIDGE_LOG", "WARNING"), stream=sys.stderr)
    first = sys.stdin.readline()
    if not first:
        return
    request = json.loads(first)
    if request.get("tz"):
        os.environ["TZ"] = request["tz"]
        time.tzset()
    _loop = VirtualClockLoop(request.get("startMs", time.time() * 1000) / 1000)
    asyncio.set_event_loop(_loop)
    harness = Harness()

    line: Optional[str] = first
    while line:
        try:
            reply = _loop.run_until_complete(harness.handle(json.loads(line)))
        except Exception as e:  # noqa: BLE001 - every failure goes back to the simulator
            logging.exception("harness request failed")
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(reply, default=str) + "\n")
        sys.stdout.flush()
        line = sys.stdin.readline()


if __name__ == "__main__":
    main()

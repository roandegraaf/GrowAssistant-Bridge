"""ESPHome Integration Implementation.

Connects to one or more ESPHome devices over the native API
(default port 6053) using the `aioesphomeapi` library, subscribes to
entity states, and surfaces them as GrowAssistant sensors/actuators.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from app.integrations import Integration, register_integration
from app.schemas.config_schemas import ESPHomeIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


# Map ESPHome `device_class` strings to GrowAssistant device types.
DEVICE_CLASS_TO_TYPE: dict[str, str] = {
    "temperature": "temperature",
    "humidity": "humidity",
    "pressure": "pressure",
    "atmospheric_pressure": "pressure",
    "illuminance": "light_sensor",
    "ph": "ph",
    # Note: ESPHome `device_class: moisture` is ambiguous (soil sensor vs.
    # leak detector vs. tank level). Soil moisture must be mapped explicitly
    # via the `entities:` block in config.
}

# Categorise a GrowAssistant type as sensor vs actuator.
SENSOR_TYPES = {
    "temperature",
    "humidity",
    "pressure",
    "light_sensor",
    "water_level",
    "soil_moisture",
    "ph",
    "ec",
    "flow",
}
ACTUATOR_TYPES = {"light", "fan", "heater", "pump", "light_switch", "switch"}


@register_integration
class ESPHomeIntegration(Integration):
    """Talk to ESPHome devices over their native API."""

    CONFIG_SCHEMA = ESPHomeIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._devices_cfg: dict[str, dict[str, Any]] = self.config.get("devices", {}) or {}
        self._reconnect_interval: int = int(self.config.get("reconnect_interval", 10))

        # Per ESPHome device runtime state.
        # device_id -> {
        #   "client": APIClient, "task": asyncio.Task,
        #   "entities": {key: EntityState},  # latest state by entity key
        #   "by_key": {key: {"object_id", "type", "name", "category", ...}},
        #   "connected": bool,
        # }
        self._runtime: dict[str, dict[str, Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

        # Pending state updates emitted to the data collection task.
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def connect(self) -> bool:
        if not self.config.get("enabled", False):
            return False

        if not self._devices_cfg:
            logger.warning("ESPHome integration enabled but no devices configured.")
            return False

        try:
            import aioesphomeapi  # noqa: F401
        except ImportError:
            logger.error("aioesphomeapi is not installed. Run: pip install aioesphomeapi")
            return False

        self._loop = asyncio.get_running_loop()

        any_started = False
        for device_id, device_cfg in self._devices_cfg.items():
            if not isinstance(device_cfg, dict):
                logger.error("Invalid ESPHome device config for '%s'", device_id)
                continue

            self._runtime[device_id] = {
                "client": None,
                "task": None,
                "entities": {},
                "by_key": {},
                "connected": False,
                "name": device_cfg.get("name", device_id),
            }
            task = asyncio.create_task(
                self._device_loop(device_id, device_cfg), name=f"esphome-{device_id}"
            )
            self._runtime[device_id]["task"] = task
            any_started = True

        # Wait briefly for at least one device to discover entities so that
        # register_capabilities() (called right after connect) sees them.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if any(r["by_key"] for r in self._runtime.values()):
                break
            await asyncio.sleep(0.2)

        return any_started

    async def disconnect(self) -> None:
        for device_id, state in list(self._runtime.items()):
            task: asyncio.Task | None = state.get("task")
            client = state.get("client")
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as e:
                    logger.debug("Error disconnecting ESPHome '%s': %s", device_id, e)
        self._runtime.clear()

    # -------------------------------------------------------------------
    # Per-device connect/subscribe loop
    # -------------------------------------------------------------------

    async def _device_loop(self, device_id: str, device_cfg: dict[str, Any]) -> None:
        """Connect to a single ESPHome device, subscribe to states, and reconnect on failure."""
        from aioesphomeapi import APIClient, APIConnectionError

        host = device_cfg["host"]
        port = int(device_cfg.get("port", 6053))
        password = device_cfg.get("password") or ""
        psk = device_cfg.get("encryption_key") or None
        friendly = device_cfg.get("name", device_id)

        while True:
            client = APIClient(host, port, password, noise_psk=psk)
            self._runtime[device_id]["client"] = client

            try:
                await client.connect(login=True)
                self._runtime[device_id]["connected"] = True
                logger.info("Connected to ESPHome device '%s' at %s:%s", friendly, host, port)

                entities, _services = await client.list_entities_services()
                self._index_entities(device_id, entities, device_cfg.get("entities") or {})

                def _on_state(state: Any) -> None:
                    self._handle_state(device_id, state)

                client.subscribe_states(_on_state)

                # Heartbeat: periodically poll device_info. If the link is
                # dead, this raises APIConnectionError and we reconnect.
                while True:
                    await asyncio.sleep(30)
                    await client.device_info()

            except asyncio.CancelledError:
                raise
            except APIConnectionError as e:
                logger.warning("ESPHome '%s' connection error: %s", friendly, e)
            except Exception as e:
                logger.exception("Unexpected error in ESPHome loop for '%s': %s", friendly, e)
            finally:
                self._runtime[device_id]["connected"] = False
                try:
                    await client.disconnect()
                except Exception:
                    pass

            await asyncio.sleep(self._reconnect_interval)

    def _index_entities(
        self,
        device_id: str,
        entities: list[Any],
        explicit_map: dict[str, Any],
    ) -> None:
        """Build the per-device entity-key index used to dispatch state updates."""
        runtime = self._runtime[device_id]
        runtime["by_key"] = {}

        # Normalise explicit map keys for case-insensitive matching against
        # ESPHome `object_id` / `name` / friendly forms.
        explicit_lc = {k.lower(): v for k, v in explicit_map.items()}

        for entity in entities:
            object_id = getattr(entity, "object_id", None) or ""
            name = getattr(entity, "name", None) or ""
            device_class = getattr(entity, "device_class", "") or ""
            unit = getattr(entity, "unit_of_measurement", "") or ""
            key = getattr(entity, "key", None)
            if key is None:
                continue

            override = (
                explicit_lc.get(object_id.lower())
                or explicit_lc.get(name.lower())
                or explicit_lc.get(name.lower().replace(" ", "_"))
            )

            mapping = self._resolve_mapping(entity, override)
            if mapping is None:
                logger.debug(
                    "Skipping unmapped ESPHome entity '%s' (device_class=%r unit=%r)",
                    name or object_id,
                    device_class,
                    unit,
                )
                continue

            mapping["object_id"] = object_id or name
            mapping["entity_name"] = name
            mapping["esphome_kind"] = type(entity).__name__
            runtime["by_key"][key] = mapping

        logger.info(
            "ESPHome '%s': mapped %d/%d entities",
            runtime["name"],
            len(runtime["by_key"]),
            len(entities),
        )

    def _resolve_mapping(
        self, entity: Any, override: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Decide how to expose a single ESPHome entity to GrowAssistant."""
        kind = type(entity).__name__
        device_class = (getattr(entity, "device_class", "") or "").lower()
        object_id = getattr(entity, "object_id", "") or getattr(entity, "name", "")

        # Default category from the ESPHome entity kind.
        if kind in ("SwitchInfo", "LightInfo", "FanInfo", "ButtonInfo"):
            default_category = "actuator"
        else:
            default_category = "sensor"

        if override:
            ga_type = override.get("type")
            if not ga_type:
                return None
            category = override.get("category") or default_category
            name = override.get("name") or object_id
            return {
                "type": ga_type,
                "category": category,
                "name": name,
            }

        # Auto-mapping: only sensor-like entities with a recognised device_class.
        if default_category == "sensor":
            ga_type = DEVICE_CLASS_TO_TYPE.get(device_class)
            if ga_type is None:
                return None
            return {
                "type": ga_type,
                "category": "sensor",
                "name": object_id,
            }

        # Actuators are only auto-mapped if the user opts in via explicit config.
        return None

    def _handle_state(self, device_id: str, state: Any) -> None:
        """Callback (called from aioesphomeapi) for entity state updates."""
        runtime = self._runtime.get(device_id)
        if runtime is None:
            return
        key = getattr(state, "key", None)
        if key is None or key not in runtime["by_key"]:
            return

        if getattr(state, "missing_state", False):
            return

        value = self._extract_value(state)
        if value is None:
            return
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return

        mapping = runtime["by_key"][key]
        runtime["entities"][key] = {"value": value, "ts": time.time(), **mapping}

        # Push to the inbox so the next receive_data() call drains it. The
        # sample carries the explicit entity_id matching register_capabilities
        # (`esphome.<device_name>_<mapping_name>`) so it joins its manifest
        # entity — the old `<device_id>:<object_id>` device_id never did.
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(
                    self._inbox.put_nowait,
                    self.telemetry_sample(
                        f"{runtime['name']}_{mapping['name']}",
                        value,
                        domain="esphome",
                        device=runtime["name"],
                        entity=mapping["object_id"],
                        type=mapping["type"],
                    ),
                )
            except RuntimeError:
                pass

    @staticmethod
    def _extract_value(state: Any) -> Any:
        """Pull a serialisable value out of an aioesphomeapi state object."""
        for attr in ("state", "value"):
            if hasattr(state, attr):
                v = getattr(state, attr)
                if isinstance(v, bool):
                    return "on" if v else "off"
                return v
        return None

    # -------------------------------------------------------------------
    # Integration interface
    # -------------------------------------------------------------------

    async def send_data(self, data: dict[str, Any]) -> bool:
        device_id = data.get("device_id") or data.get("device")
        entity = data.get("entity") or data.get("target_id")
        action = data.get("action") or ("on" if data.get("value") else "off")
        return await self._send_command(device_id, entity, action, data)

    async def receive_data(self) -> AsyncGenerator[dict[str, Any], None]:
        while not self._inbox.empty():
            try:
                item = self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

            yield item

    async def get_device_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for device_id, runtime in self._runtime.items():
            for state in runtime["entities"].values():
                key = f"{runtime['name']}.{state['object_id']}"
                result[key] = {
                    "type": state["type"],
                    "value": state["value"],
                    "timestamp": state["ts"],
                }
        return result

    def register_capabilities(self, registry: DeviceRegistry) -> None:
        for device_id, runtime in self._runtime.items():
            for mapping in runtime["by_key"].values():
                ga_type = mapping["type"]
                category = mapping["category"]
                # Disambiguate names across ESPHome devices.
                reg_name = f"{runtime['name']}_{mapping['name']}"
                if category == "actuator" or ga_type in ACTUATOR_TYPES:
                    registry.register_actuator(
                        actuator_name=reg_name,
                        integration_name=self.name,
                        domain="esphome",
                        device_type=ga_type,
                    )
                else:
                    registry.register_sensor(
                        sensor_name=reg_name,
                        integration_name=self.name,
                        domain="esphome",
                        device_type=ga_type,
                    )

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        # target_id is `<device_name>_<entity_object_id>`. Resolve back.
        device_id, entity_object_id = self._resolve_target(target_id)
        if device_id is None:
            logger.error("Unknown ESPHome target: %s", target_id)
            return False
        return await self._send_command(device_id, entity_object_id, action, payload)

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _resolve_target(self, target_id: str) -> tuple[str | None, str | None]:
        for device_id, runtime in self._runtime.items():
            prefix = f"{runtime['name']}_"
            if target_id.startswith(prefix):
                object_id = target_id[len(prefix) :]
                for mapping in runtime["by_key"].values():
                    if mapping["name"] == object_id or mapping["object_id"] == object_id:
                        return device_id, mapping["object_id"]
        return None, None

    async def _send_command(
        self,
        device_id: str | None,
        entity_object_id: str | None,
        action: str,
        payload: dict[str, Any],
    ) -> bool:
        if not device_id or not entity_object_id:
            return False
        runtime = self._runtime.get(device_id)
        if runtime is None or not runtime.get("connected") or runtime.get("client") is None:
            logger.error("ESPHome device '%s' is not connected", device_id)
            return False

        match_key: int | None = None
        kind: str | None = None
        for key, mapping in runtime["by_key"].items():
            if mapping["object_id"] == entity_object_id or mapping["name"] == entity_object_id:
                match_key = key
                kind = mapping.get("esphome_kind")
                break
        if match_key is None:
            logger.error("Unknown ESPHome entity '%s' on device '%s'", entity_object_id, device_id)
            return False

        client = runtime["client"]
        action_lc = (action or "").lower()
        try:
            if kind == "SwitchInfo":
                client.switch_command(match_key, action_lc == "on")
            elif kind == "LightInfo":
                client.light_command(key=match_key, state=(action_lc != "off"))
            elif kind == "FanInfo":
                client.fan_command(key=match_key, state=(action_lc != "off"))
            elif kind == "ButtonInfo":
                client.button_command(match_key)
            else:
                logger.warning("ESPHome entity kind '%s' does not support commands", kind)
                return False
            return True
        except Exception as e:
            logger.error("Error sending ESPHome command: %s", e)
            return False

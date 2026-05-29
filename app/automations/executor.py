"""Execute a rule's ``call`` action against a bridge integration.

This is the make-or-break seam between the app's HA-style rule vocabulary and
the bridge's device interface (Phase-4 decision 9):

* The rule carries an HA **service** name (``turn_on``/``turn_off``/``set_value``
  /…) and an **entity id** (``switch.tent1_fan``).
* ``Integration.execute_command`` wants a bridge **action** string
  (``on``/``off``/``set``/``speed``/…) and the device **name**
  (``tent1_fan``) — *not* the entity id.

So a ``call`` resolves the entity to its ``DeviceInfo`` (giving the device name
and owning integration), translates the service to a bridge action, and invokes
``integration.execute_command(device.name, action, payload)``. Unknown services
pass through lowercased so a rule can target a device-specific action directly
(e.g. ``service: speed``).

Lazy resolution (advisor): if the entity is not in the registry yet (a device
that appears later), the call logs and no-ops rather than erroring — the rule
self-corrects once the device registers, with no engine rebuild needed.

Optimistic write-back: actuators are never polled into the StateStore (GPIO
``receive_data`` yields input pins only), so after a successful command the
commanded state is written back (``on`` → ``1``, ``off`` → ``0``, ``set`` →
the payload ``value``) so switch state-triggers and ``wait_for_state`` on the
actuator observe the change.
"""

import logging
from typing import Any, Callable, Optional

from app.registry import registry as default_registry

from .state_store import StateStore

logger = logging.getLogger(__name__)

# HA service → bridge action. Anything not listed passes through lowercased, so
# device-specific actions (speed/level/temperature) can be used directly.
SERVICE_TO_ACTION: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "toggle": "toggle",
    "set_value": "set",
    "set_percentage": "speed",
    "set_temperature": "temperature",
}

# Bridge action → the scalar to write back to the StateStore after success.
# ``set`` is handled separately (it carries an explicit value in the payload).
_WRITE_BACK: dict[str, Any] = {"on": 1, "off": 0}


def translate_service(service: str) -> str:
    """Translate an HA service name to a bridge action string."""
    return SERVICE_TO_ACTION.get(service, service.lower())


# integration_name → Integration (or None if not loaded / not present).
IntegrationProvider = Callable[[str], Optional[Any]]


class ActionExecutor:
    """Resolves and executes ``call`` actions on bridge integrations."""

    def __init__(
        self,
        integration_provider: IntegrationProvider,
        state_store: Optional[StateStore] = None,
        registry: Any = default_registry,
    ) -> None:
        self._integration_for = integration_provider
        self._state_store = state_store
        self._registry = registry

    async def call(
        self, entity_id: str, service: str, data: Optional[dict[str, Any]] = None
    ) -> bool:
        """Execute ``service`` on ``entity_id``. Returns True on success.

        No-ops (returns False) when the entity or its integration is not
        currently available — lazy resolution for devices that appear later.
        """
        payload = data or {}
        device = self._registry.get_device(entity_id)
        if device is None:
            logger.info("call: entity '%s' not in registry yet — skipping", entity_id)
            return False

        integration = self._integration_for(device.integration_name)
        if integration is None:
            logger.warning(
                "call: no loaded integration '%s' for entity '%s' — skipping",
                device.integration_name,
                entity_id,
            )
            return False

        action = translate_service(service)
        try:
            ok = await integration.execute_command(device.name, action, payload)
        except Exception:
            logger.exception("call: execute_command failed for %s (%s)", entity_id, action)
            return False

        if ok:
            await self._write_back(entity_id, action, payload)
        return bool(ok)

    async def _write_back(self, entity_id: str, action: str, payload: dict[str, Any]) -> None:
        """Optimistically reflect the commanded state in the StateStore."""
        if self._state_store is None:
            return
        if action == "set" and "value" in payload:
            await self._state_store.set(entity_id, payload["value"])
        elif action in _WRITE_BACK:
            await self._state_store.set(entity_id, _WRITE_BACK[action])

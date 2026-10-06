"""Execute one app → bridge command (protocol §9.1) and say how to ack it.

Shared by the MQTT command loop in ``main.py`` and the grow simulator harness,
so both resolve targets, fire events and report results identically.
"""

import logging
from collections.abc import Mapping
from typing import Any, Optional

from app.integrations import Integration

logger = logging.getLogger(__name__)


async def run_command(
    command: dict[str, Any],
    *,
    registry: Any,
    integrations: Mapping[str, Integration],
    engine: Optional[Any],
) -> tuple[bool, str]:
    """Run ``command`` and return the ``(success, message)`` its ack carries."""
    target_type = command.get("targetType")
    target_id = command.get("targetId")
    action = command.get("action")
    payload = command.get("payload", {})

    if not all([target_type, target_id, action]):
        logger.error(f"Command missing required fields: {command}")
        return False, "Missing required fields"

    if target_type == "event":
        if engine is None or action != "fire":
            return False, "Events need the automation engine and action 'fire'"
        data = payload.get("data") if isinstance(payload, dict) else None
        engine.emit_event(target_id, data if isinstance(data, dict) else {})
        return True, "Event fired"

    try:
        # Primary path (§16.1): targetId is the full `<domain>.<name>` entity
        # id — unambiguous across integrations, resolved exactly like the
        # automations executor resolves rule targets. The bare device name
        # remains accepted for backward compatibility with older app versions.
        local_name = target_id
        if "." in target_id:
            device = registry.get_device(target_id)
            if device is None:
                logger.error(f"Unknown entity id in command: {target_id}")
                return False, f"Unknown entity: {target_id}"
            integration_name = device.integration_name
            local_name = device.name
        elif target_type == "sensor":
            integration_name = registry.get_sensor_integration(target_id)
        elif target_type == "actuator":
            integration_name = registry.get_actuator_integration(target_id)
        else:
            logger.error(f"Unknown target type: {target_type}")
            return False, f"Unknown target type: {target_type}"

        if not integration_name or integration_name not in integrations:
            logger.error(f"No integration found for {target_type} {target_id}")
            return False, f"No integration for {target_type} {target_id}"

        success = await integrations[integration_name].execute_command(local_name, action, payload)

        # Seed a real lifecycle event so rules can react to app-issued commands
        # (fresh chain — a rule reacting to this is depth-guarded).
        if engine is not None:
            engine.emit_event(
                "command_executed",
                {"targetId": target_id, "action": action, "success": bool(success)},
            )

        return bool(success), (
            "Command executed successfully" if success else "Command execution failed"
        )
    except Exception as e:
        logger.error(f"Error processing command: {e}")
        return False, f"Error: {e}"

"""A fake third-party integration, written only against the public contract.

The contract tests load it the way the bridge loads any external integration
(a drop-in file in an external integrations directory), so it proves a new
vendor needs no core changes. It is never shipped: it lives under tests/.
"""

from collections.abc import AsyncGenerator
from typing import Any

from app.entity_id import derive_domain
from app.integrations import Integration, register_integration
from app.registry import DeviceCategory


@register_integration
class AcmeIntegration(Integration):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.vent_on = False
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    async def connect(self) -> bool:
        return True

    def register_capabilities(self, registry) -> None:
        domain = derive_domain(self.name)
        registry.register_device(
            name="grow_temp",
            domain=domain,
            device_type="thermo_probe",
            category=DeviceCategory.SENSOR,
            integration_name=self.name,
            metadata={"unit": "°C", "device_class": "temperature"},
        )
        registry.register_device(
            name="vent",
            domain=domain,
            device_type="blower",
            category=DeviceCategory.ACTUATOR,
            integration_name=self.name,
            capabilities=["on", "off"],
            metadata={"device_class": "exhaust_fan"},
        )

    async def receive_data(self) -> AsyncGenerator[dict[str, Any], None]:
        yield self.telemetry_sample("grow_temp", 23.4)
        yield self.telemetry_sample("vent", self.vent_on)

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        self.commands.append((target_id, action, payload))
        if target_id != "vent" or action not in ("on", "off"):
            return False
        self.vent_on = action == "on"
        return True

    async def send_data(self, data: dict[str, Any]) -> bool:
        return False

    async def get_device_data(self) -> dict[str, Any]:
        return {"grow_temp": 23.4, "vent": self.vent_on}

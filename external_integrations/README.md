# External Integrations for GrowAssistant Bridge

This directory is where you can add custom integrations for the GrowAssistant Bridge.

## Quick Start

### 1. Copy the Template

```bash
cp dht_sensor.py my_integration.py
```

### 2. Implement Your Integration

```python
from typing import Any, Dict, Generator, TYPE_CHECKING
from app.integrations import Integration, register_integration

if TYPE_CHECKING:
    from app.registry import DeviceRegistry


@register_integration
class MyIntegration(Integration):
    """My custom integration."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.devices = self.config.get("devices", {})

    async def connect(self) -> bool:
        return self.config.get("enabled", False)

    async def send_data(self, data: Dict[str, Any]) -> bool:
        return True

    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        yield {"device": "my_device", "value": 42}

    async def get_device_data(self) -> Dict[str, Any]:
        return {"my_device": {"value": 42}}

    # NEW: Self-registration
    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        for name, device in self.devices.items():
            if device["type"] in ["temperature", "humidity"]:
                registry.register_sensor(name, self.name, domain="my", device_type=device["type"])
            else:
                registry.register_actuator(name, self.name, domain="my", device_type=device["type"])

    # NEW: Unified commands
    async def execute_command(self, target_id: str, action: str, payload: Dict[str, Any]) -> bool:
        return await self.send_data({"device": target_id, "action": action, **payload})
```

### 3. Add Configuration

In `config.yaml`:

```yaml
integrations:
  my:  # Must match get_config_key() return value (default: class name without 'Integration', lowercase)
    enabled: true
    devices:
      '0':
        name: my_sensor
        type: temperature
```

### 4. Restart

Restart GrowAssistant Bridge. Your integration will be auto-discovered.

---

## New Architecture (Home Assistant-style)

The integration system now uses **self-registration**:

| Feature | Description |
|---------|-------------|
| **Auto-discovery** | Integrations are found by config key, no code changes needed |
| **Self-registration** | Implement `register_capabilities()` to register your devices |
| **Domain-based IDs** | Entity IDs like `mqtt.temperature`, `gpio.pump1` |
| **Config validation** | Optional Pydantic schemas for type-safe config |
| **Unified commands** | `execute_command()` replaces direct `send_data()` calls |

---

## Key Methods

### Required

| Method | Description |
|--------|-------------|
| `__init__(config)` | Initialize with config dict |
| `connect()` | Connect to device, return `True` on success |
| `send_data(data)` | Send command, return `True` on success |
| `receive_data()` | Yield data from devices |
| `get_device_data()` | Return current state dict |

### New Optional Methods

| Method | Description |
|--------|-------------|
| `get_config_key()` | Return config key (default: class name without 'Integration', lowercase) |
| `register_capabilities(registry)` | Self-register sensors/actuators |
| `execute_command(target_id, action, payload)` | Handle commands from API |
| `disconnect()` | Clean up resources |
| `apply_settings(settings)` | Apply settings from API |

---

## Emitting telemetry

Telemetry flows by **yielding data points from `receive_data()`**. The bridge's
collection loop tags each point with your integration name, derives its
`<domain>.<name>` entity id, and publishes it to the app over MQTT — you do not
call any API/logging method yourself.

```python
async def receive_data(self):
    # Yield one dict per reading; the key the bridge uses for the entity name
    # depends on the integration (e.g. `sensor`, `endpoint_name`, `pin_name`).
    yield {"sensor": "temp1", "value": 25.5}
```

Commands from the app arrive via `execute_command(target_id, action, payload)`.

---

## Config Validation (Optional)

```python
from pydantic import BaseModel, Field

class MyConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = Field(default=8080, ge=1, le=65535)

@register_integration
class MyIntegration(Integration):
    CONFIG_SCHEMA = MyConfig  # Validated in __init__
```

---

## Device Registration

```python
def register_capabilities(self, registry):
    # Register a sensor
    registry.register_sensor(
        sensor_name="temp1",
        integration_name=self.name,
        domain="my",  # Entity ID: my.temp1
        device_type="temperature",
    )

    # Register an actuator
    registry.register_actuator(
        actuator_name="pump1",
        integration_name=self.name,
        domain="my",  # Entity ID: my.pump1
        device_type="pump",
    )
```

---

## Files

| File | Description |
|------|-------------|
| `dht_sensor.py` | DHT sensor example (a good starting template) |
| `dht_config.yaml` | Example configuration for the DHT sensor |
| `climate_control.py` | Actuator/control-loop example (hysteresis + apply_settings) |

---

## Additional Resources

- [Custom Integrations Guide](../docs/custom_integrations.md) - Full documentation
- [Developer Guide](../docs/developer_guide.md) - Architecture details
- [Built-in integrations](../app/integrations/) - GPIO, MQTT, HTTP, Serial examples
- [Config schemas](../app/schemas/config_schemas.py) - Pydantic schema examples

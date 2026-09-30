# Developing Custom Integrations for GrowAssistant Bridge

This guide covers everything you need to know to develop custom integrations for the GrowAssistant Bridge using the **new self-registration architecture** (Home Assistant-style modularity).

## Table of Contents

1. [Overview](#overview)
2. [Architecture Overview](#architecture-overview)
3. [Quick Start](#quick-start)
4. [Integration Lifecycle](#integration-lifecycle)
5. [Self-Registration Pattern](#self-registration-pattern)
6. [Configuration Validation](#configuration-validation)
7. [Device Registry](#device-registry)
   - [Device classes](#device-classes)
8. [The bridge contract](#the-bridge-contract): entity ids, telemetry, commands, calibration, contract tests
9. [Complete Integration Example](#complete-integration-example)
10. [Best Practices](#best-practices)
11. [Troubleshooting](#troubleshooting)
12. [Migration Guide](#migration-guide)

---

## Overview

GrowAssistant Bridge uses a **fully modular, self-registering integration system** inspired by Home Assistant. This architecture allows you to:

- **Add new integrations without modifying core code** - just drop in a new file
- **Self-register devices** - each integration registers its own sensors and actuators
- **Validate configuration** - use Pydantic schemas for type-safe config
- **Domain-based device IDs** - prevents naming collisions (e.g., `mqtt.temperature`, `gpio.pump1`)

### What Changed?

| Old Pattern | New Pattern |
|-------------|-------------|
| Hardcoded class mappings in `main.py` | Auto-discovery by config key |
| Capability registration in `main.py` | Self-registration in integration |
| No config validation | Pydantic schema validation |
| Flat device names | Domain-qualified entity IDs |
| `send_data()` for commands | `execute_command()` unified interface |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      GrowAssistant Bridge                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │    MQTT      │    │    GPIO      │    │   Custom     │      │
│  │ Integration  │    │ Integration  │    │ Integration  │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         └───────────────────┼───────────────────┘               │
│                             ▼                                   │
│                  ┌──────────────────┐                           │
│                  │  Device Registry │                           │
│                  │  (domain.name)   │                           │
│                  └────────┬─────────┘                           │
│                           │                                     │
│                           ▼                                     │
│                  ┌──────────────────┐                           │
│                  │   API Client     │                           │
│                  └──────────────────┘                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| `Integration` base class | `app/integrations/__init__.py` | Abstract base for all integrations |
| `@register_integration` | `app/integrations/__init__.py` | Decorator for auto-registration |
| `DeviceRegistry` | `app/registry.py` | Tracks all sensors/actuators |
| Config Schemas | `app/schemas/config_schemas.py` | Pydantic validation models |

---

## Quick Start

### Step 1: Create Your Integration File

Create a new file in `external_integrations/` (or `app/integrations/your_integration/`):

```python
"""My Custom Integration."""
from typing import Any, Dict, Generator, TYPE_CHECKING

from app.integrations import Integration, register_integration

if TYPE_CHECKING:
    from app.registry import DeviceRegistry


@register_integration
class MyIntegration(Integration):
    """Integration for my custom device."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.devices = self.config.get("devices", {})
        self.devices_by_name = [d["name"] for d in self.devices.values()]

    async def connect(self) -> bool:
        """Connect to devices."""
        if not self.config.get("enabled", False):
            return False
        return True

    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data to device."""
        return True

    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Yield one sample per registered sensor (see "Telemetry")."""
        for name in self.devices_by_name:
            yield self.telemetry_sample(name, 42)

    async def get_device_data(self) -> Dict[str, Any]:
        """Get current state."""
        return {"my_device": {"value": 42}}

    # NEW: Self-registration
    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register sensors and actuators."""
        for device in self.devices.values():
            if device["type"] in ["temperature", "humidity"]:
                registry.register_sensor(
                    sensor_name=device["name"],
                    integration_name=self.name,
                    domain="my",
                    device_type=device["type"],
                )
            else:
                registry.register_actuator(
                    actuator_name=device["name"],
                    integration_name=self.name,
                    domain="my",
                    device_type=device["type"],
                )

    # NEW: Unified command interface
    async def execute_command(
        self,
        target_id: str,
        action: str,
        payload: Dict[str, Any]
    ) -> bool:
        """Execute command on device."""
        return await self.send_data({
            "device": target_id,
            "action": action,
            **payload,
        })
```

### Step 2: Add Configuration

In `config.yaml`:

```yaml
integrations:
  my:  # Must match get_config_key() return value
    enabled: true
    devices:
      '0':
        name: my_sensor
        type: temperature
      '1':
        name: my_pump
        type: pump
```

### Step 3: Restart

Restart GrowAssistant Bridge. Your integration will be auto-discovered and loaded.

---

## Integration Lifecycle

```
1. Discovery     ─→  discover_integrations() finds your module
2. Registration  ─→  @register_integration decorator registers class
3. Instantiation ─→  __init__(config) called with your config section
4. Validation    ─→  CONFIG_SCHEMA validated (if defined)
5. Connection    ─→  connect() establishes connection
6. Capabilities  ─→  register_capabilities() registers devices
7. Running       ─→  send_data(), receive_data() called during operation
8. Shutdown      ─→  disconnect() cleans up resources
```

### Required Methods

| Method | Signature | Purpose |
|--------|-----------|---------|
| `__init__` | `(config: Dict[str, Any])` | Initialize with config |
| `connect` | `() -> bool` | Establish connection |
| `send_data` | `(data: Dict[str, Any]) -> bool` | Send command/data |
| `receive_data` | `() -> Generator[Dict, None, None]` | Yield received data |
| `get_device_data` | `() -> Dict[str, Any]` | Return current state |

### Optional Methods

| Method | Signature | Purpose |
|--------|-----------|---------|
| `disconnect` | `() -> None` | Clean up resources |
| `register_capabilities` | `(registry: DeviceRegistry) -> None` | Self-register devices |
| `execute_command` | `(target_id, action, payload) -> bool` | Handle commands |
| `apply_settings` | `(settings: Dict) -> bool` | Apply API settings |
| `handle_action` | `(action_data: Dict) -> bool` | Handle API actions |
| `get_config_key` | `() -> str` | Custom config key |

---

## Self-Registration Pattern

### How It Works

Instead of hardcoding device registration in `main.py`, each integration registers its own devices:

```python
def register_capabilities(self, registry: "DeviceRegistry") -> None:
    """Called after connect() succeeds."""
    # Register sensors
    registry.register_sensor(
        sensor_name="temperature",
        integration_name=self.name,
        domain="my_integration",  # Your domain
        device_type="temperature",
    )

    # Register actuators
    registry.register_actuator(
        actuator_name="pump1",
        integration_name=self.name,
        domain="my_integration",
        device_type="pump",
    )
```

### Entity IDs

Devices are identified by domain-qualified entity IDs:

```
Format: domain.device_name

Examples:
  - mqtt.temperature
  - gpio.pump1
  - my_integration.sensor1
```

This prevents collisions when multiple integrations have devices with similar names.

### Config Key Mapping

The config key determines which section of `config.yaml` your integration receives:

```python
@classmethod
def get_config_key(cls) -> str:
    """Return config key (default: class name without 'Integration', lowercase)."""
    return "my_custom"  # Matches 'my_custom:' in config.yaml
```

Default behavior:
- `MQTTIntegration` → `"mqtt"`
- `HTTPIntegration` → `"http"`
- `MyCustomIntegration` → `"mycustom"`

---

## Configuration Validation

### Using Pydantic Schemas

Define a schema for type-safe configuration:

```python
from pydantic import BaseModel, Field
from typing import Dict, Optional

class MyDeviceConfig(BaseModel):
    name: str
    type: str
    port: int = Field(ge=1, le=65535)

class MyIntegrationConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    devices: Dict[str, MyDeviceConfig] = {}

@register_integration
class MyIntegration(Integration):
    CONFIG_SCHEMA = MyIntegrationConfig  # Set this!

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)  # Validation happens here

        # Access validated config
        if self.validated_config:
            host = self.validated_config.host
            devices = self.validated_config.devices
```

### Built-in Schemas

See `app/schemas/config_schemas.py` for examples:

- `GPIOIntegrationConfig`
- `MQTTIntegrationConfig`
- `HTTPIntegrationConfig`
- `SerialIntegrationConfig`

---

## Device Registry

### Registration Methods

```python
# Register a sensor
registry.register_sensor(
    sensor_name="temp1",
    integration_name=self.name,
    domain="my_domain",      # Optional, derived from integration name
    device_type="temperature",  # Optional, defaults to sensor_name
)

# Register an actuator
registry.register_actuator(
    actuator_name="pump1",
    integration_name=self.name,
    domain="my_domain",
    device_type="pump",
)

# Full control with register_device()
from app.registry import DeviceCategory

registry.register_device(
    name="smart_pump",
    domain="my_domain",
    device_type="dosing_pump",
    category=DeviceCategory.ACTUATOR,
    integration_name=self.name,
    capabilities=["dispense", "calibrate", "prime"],
    metadata={"model": "DP-100", "max_flow": 100},
)
```

### Device classes

Every manifest entry carries an optional `deviceClass`: what the entity *is*,
independent of the integration that provides it. The app never looks at
integration names; it binds each space's roles ("the tent temperature sensor",
"the exhaust fan") to entities by `deviceClass`, and plan targets, alerts and
flows ask for a role. An integration that reports a correct class works with
every guidance feature without app changes.

The class is resolved in `DeviceRegistry._device_class` (`app/registry.py`):

1. `metadata["device_class"]`, when the integration sets it, wins.
2. Otherwise the `device_type` is mapped, per category:

| Category | `device_type` | `deviceClass` |
|----------|---------------|---------------|
| sensor   | `temperature`, `humidity`, `co2`, `ppfd`, `ph`, `ec`, `soil_moisture`, `water_level`, `pressure`, `flow` | same as the type |
| sensor   | `light_sensor`, `illuminance` | `illuminance` |
| actuator | `light`, `light_switch` | `light` |
| actuator | `fan`, `exhaust_fan`, `intake_fan`, `circulation_fan`, `humidifier`, `dehumidifier`, `heater`, `pump` | same as the type |
| actuator | `humidity` | `humidifier` |
| camera   | any | `camera` |

3. Anything else serializes as `null`. The entity still works (widgets,
   commands, flows); the user just binds it to a role by hand.

Set the class explicitly when your `device_type` is generic or misleading:

```python
registry.register_device(
    name="probe1",
    domain="my",
    device_type="analog_in",
    category=DeviceCategory.SENSOR,
    integration_name=self.name,
    metadata={"unit": "%", "device_class": "soil_moisture"},
)
```

`deviceClass` is a wire-only field: it is not part of the manifest hash
(`docs/bridge-protocol.md` §6.2), so adding or changing it never breaks hash
parity. The app stores it on `Entity.deviceClass` and ignores classes it
doesn't know.

The contract tests in `tests/test_telemetry_contract.py` assert the serialized
`deviceClass` per integration next to the telemetry join check; add a case
there when you add an integration.

### Querying the Registry

```python
from app.registry import registry

# Get device by entity ID
device = registry.get_device("mqtt.temperature")

# Find by name (searches all domains)
device = registry.find_device("temperature")

# Get devices by domain
gpio_devices = registry.get_devices_by_domain("gpio")

# Get devices by type
pumps = registry.get_devices_by_type("pump")

# Get devices by integration
my_devices = registry.get_devices_by_integration("MyIntegration")
```

---

## The bridge contract

This is everything the app relies on. An integration that follows it works with
every app feature (widgets, roles, plan targets, alerts, flows, calibration)
without any app change. The wire format itself is in
[`bridge-protocol.md`](./bridge-protocol.md).

### Entity ids and entity domains

Every device is identified by a single entity id, `<integration_domain>.<name>`:

- `integration_domain` comes from your class name via
  `app.entity_id.derive_domain` (`AcmeIntegration` → `acme`,
  `MQTTIntegration` → `mqtt`). Pass `domain=derive_domain(self.name)` when you
  call `registry.register_device(...)`, or override it for a custom domain (the
  climate integration registers `climate.*`).
- `name` is your device's local name, exactly as you registered it.

The entity id is the join key between the manifest and telemetry, and the
target of every command. Keep it stable: renaming a device creates a new entity
in the app.

The manifest also carries `entityDomain`, the Home Assistant-style kind of
entity. It is derived from the registration, not from the id:

| Registration | `entityDomain` |
|--------------|----------------|
| `DeviceCategory.SENSOR` | `sensor` |
| `DeviceCategory.CAMERA` | `camera` |
| actuator with `device_type="light"` | `light` |
| actuator with a `speed`, `level`, `temperature` or `set` capability | `number` |
| any other actuator | `switch` |

The app stores it as `Entity.domain`, and it decides how the entity is shown
and which commands the UI offers. Actuators are `writable: true` in the manifest.

Next to `entityDomain`, set a `deviceClass` (see [Device classes](#device-classes))
so the app can suggest the entity for a space role.

### Telemetry

`receive_data()` yields samples. Build every sample with `telemetry_sample()`:

```python
async def receive_data(self):
    yield self.telemetry_sample("grow_temp", 23.4)
    yield self.telemetry_sample("vent", self.vent_on)
```

`telemetry_sample(name, value, *, domain=None, **extra)` returns
`{"entity_id": "<domain>.<name>", "value": value, **extra}`. Two rules:

- `entity_id` is explicit and equals an id you registered. Without it the core
  falls back to guessing from keys like `device` or `name`, and a guessed id
  that doesn't match a registration never shows up in the app.
- `value` is top-level. Numbers, booleans and short strings (`"on"`) are all
  fine. Send raw readings, never pre-converted or pre-calibrated ones.

The collection loop then tags the sample with a timestamp and your integration
name, applies calibration (below), feeds the automation engine's state store,
offers it to every integration's `on_telemetry()` hook, and queues it for
publishing as `{"entityId", "value", "ts"}` on `…/telemetry`.

### Commands

The app and the local automation engine both address a device by its full
entity id. On the wire (`…/cmd/{id}`), `targetId` carries that id, for example
`{"targetId": "acme.vent", "action": "on", "payload": {}}`. The core looks up
the id in the registry, finds the integration that registered it, and calls:

```python
async def execute_command(self, target_id: str, action: str, payload: dict) -> bool:
    ...
```

`target_id` here is your **local name** (`"vent"`), not the full id. Return
`True` only when the device really did what was asked. Return `False` for
actions you don't support: never coerce an unknown action into on or off. The
result is acknowledged to the app on `…/cmd/{id}/ack`.

Actions come from the registered capabilities (`on`, `off`, `speed`, `level`,
`set`, …). Payload fields such as `value` ride in `payload`.

### Calibration

Users can set a per-entity calibration in the app. It is `calibrated = raw ×
scale + offset`, where the offset defaults to 0 and the scale defaults to 1. The
app sends calibrations to the bridge inside the retained rule-set payload
(`bridge-protocol.md` §10). The core applies them in one place, the collection
loop, before a sample is published, stored for the engine or fanned out. So the
app's charts and alerts see the same corrected value that local flows evaluate.

Integrations do nothing for this. It only works when you follow the telemetry
rules above:

- The value must be the top-level `value`. A reading nested under `data` is
  published but not calibrated.
- The value must be numeric or a numeric string. Booleans and other strings
  pass through unchanged.
- Don't calibrate inside your integration, or the correction is applied twice.

### Contract tests

`tests/test_telemetry_contract.py` holds one test class per integration. Each
one asserts two things: every yielded sample joins a registered entity id, and
the manifest carries the expected `deviceClass`. Add a class when you add an
integration.

`TestThirdPartyContract` is the template for a new vendor. It loads the fake
integration `tests/fixtures/third_party/acme.py` exactly like a file dropped
into `external_integrations/`, then checks the full path:

1. the manifest (`deviceClass`, `entityDomain`, `writable`, `unit`),
2. the telemetry published on the wire,
3. a command dispatched by full entity id, reaching `execute_command` with the
   local name.

The test touches no core code, which proves a new vendor doesn't need to either.

### Settings (legacy)

`apply_settings()` still receives the locally stored legacy `light`, `climate`
and `tank` settings at startup. New features don't use it: they go through
flows, roles and the rule set. You can leave it unimplemented, and the default
raises `NotImplementedError`, which the core treats as "not supported".

---

## Complete Integration Example

- `tests/fixtures/third_party/acme.py`: the smallest complete integration, with
  a sensor plus an actuator, `deviceClass`, telemetry and commands.
- `external_integrations/simulator.py`: fake tent sensors with realistic
  curves, which is the development default.
- `external_integrations/climate_control.py`: a control-style integration that
  follows other integrations' sensors through `on_telemetry()` and registers
  under a custom domain.

---

## Best Practices

### 1. Always Check Enabled State

```python
def __init__(self, config):
    super().__init__(config)
    if not self.config.get("enabled", False):
        logger.info(f"{self.name} is disabled")
        return
```

### 2. Use Domain-Qualified Registration

```python
# Good: Explicit domain
registry.register_sensor("temp", self.name, domain="my_domain", device_type="temperature")

# Avoid: Implicit domain (works but less clear)
registry.register_sensor("temp", self.name)
```

### 3. Define Config Schema

```python
class MyConfig(BaseModel):
    enabled: bool = False
    host: str
    port: int = Field(ge=1, le=65535)

class MyIntegration(Integration):
    CONFIG_SCHEMA = MyConfig  # Fail fast on bad config
```

### 4. Clean Up Resources

```python
async def disconnect(self):
    if self._task:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
    if self._connection:
        await self._connection.close()
```

### 5. Log Appropriately

```python
logger.debug("Detailed info for debugging")
logger.info("Normal operation events")
logger.warning("Potential issues")
logger.error("Errors that need attention")
```

### 6. Handle Errors Gracefully

```python
async def send_data(self, data):
    try:
        # ... operation
        return True
    except ConnectionError as e:
        logger.error(f"Connection failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False
```

---

## Troubleshooting

### Integration Not Loading

1. Check file is in `external_integrations/` or `app/integrations/*/`
2. Verify `@register_integration` decorator is present
3. Check logs for import errors
4. Ensure class name ends with `Integration`

### Config Key Mismatch

```
Error: No integration found for config key 'myintegration'
```

Override `get_config_key()` to match your config.yaml key:

```python
@classmethod
def get_config_key(cls) -> str:
    return "myintegration"  # Matches config.yaml section
```

### Config Validation Errors

```
ConfigurationError: Invalid configuration for MyIntegration
```

Check your config matches the Pydantic schema. Enable debug logging to see details.

### Devices Not Appearing

1. Verify `register_capabilities()` is implemented
2. Check `connect()` returns `True`
3. Confirm devices are in registry: `registry.get_devices_by_integration("MyIntegration")`

### Commands Not Working

1. Ensure device is registered as actuator (not sensor)
2. Implement `execute_command()` for custom command handling
3. Check logs for error messages

---

## Migration Guide

### From Old Architecture

If you have an existing integration using the old patterns:

1. **Add `register_capabilities()`**: Move device registration logic from `main.py` into your integration class.

2. **Add `execute_command()`**: If you have custom command handling:
   ```python
   async def execute_command(self, target_id, action, payload):
       # Your command logic here
       return await self.send_data({...})
   ```

3. **Add `CONFIG_SCHEMA`** (optional but recommended):
   ```python
   CONFIG_SCHEMA = MyIntegrationConfig
   ```

4. **Update config key** if needed:
   ```python
   @classmethod
   def get_config_key(cls):
       return "my_custom_key"
   ```

5. **Remove hardcoded registration** from `main.py` (if modifying core).

---

## Additional Resources

- Built-in integrations: `app/integrations/gpio/`, `mqtt/`, `http/`, `serial/`
- Minimal template: `tests/fixtures/third_party/acme.py`
- Wire protocol: `docs/bridge-protocol.md`
- Config schemas: `app/schemas/config_schemas.py`
- Device registry: `app/registry.py`

## Need Help?

Check the logs first - most issues are logged with helpful error messages. For additional support, reach out to the GrowAssistant community.

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
8. [API Communication](#api-communication)
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

    async def connect(self) -> bool:
        """Connect to devices."""
        if not self.config.get("enabled", False):
            return False
        return True

    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data to device."""
        return True

    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from devices."""
        yield {"device": "my_device", "value": 42}

    async def get_device_data(self) -> Dict[str, Any]:
        """Get current state."""
        return {"my_device": {"value": 42}}

    # NEW: Self-registration
    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register sensors and actuators."""
        for name, device in self.devices.items():
            if device["type"] in ["temperature", "humidity"]:
                registry.register_sensor(
                    sensor_name=name,
                    integration_name=self.name,
                    domain="my",
                    device_type=device["type"],
                )
            else:
                registry.register_actuator(
                    actuator_name=name,
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

## API Communication

### Logging Data

```python
from app.api_types import LogType

# Log sensor readings
self.log_data(LogType.TEMPERATURE, 25.5)
self.log_data(LogType.HUMIDITY, 65)
self.log_data(LogType.TANK_ML, 500, pump_num=1)  # With pump number
```

### Reporting Problems

```python
from app.api_types import ProblemType, ProblemStatus

self.report_problem(
    problem_type=ProblemType.TEMPERATURE,
    status=ProblemStatus.RANGE,
    description="Temperature out of range: 35.2°C",
    priority=70,
    user_can_resolve=True,
)
```

### Handling Actions

```python
from app.api_types import ActionType

async def connect(self) -> bool:
    # Register action handlers
    self.register_action_handler(ActionType.TEMPERATURE, self._handle_temp)
    self.register_action_handler(ActionType.SUPPLEMENT_ML, self._handle_dosing)
    return True

async def _handle_temp(self, action_data: Dict[str, Any]) -> bool:
    action_id = action_data.get("id")
    value = float(action_data.get("value", 0))

    # Execute the action...

    # Acknowledge completion
    self.acknowledge_action(action_id, received=True, resolved=True)
    return True

async def _handle_dosing(self, action_data: Dict[str, Any]) -> bool:
    value = float(action_data.get("value", 0))
    pump_num = action_data.get("pumpNumber")

    # Dispense nutrients...

    return True
```

### Applying Settings

```python
async def apply_settings(self, settings: Dict[str, Any]) -> bool:
    """Handle settings updates from API."""
    try:
        # Climate settings
        climate = settings.get("climate", {})
        if climate:
            temp = climate.get("temperature")
            humidity = climate.get("humidity")
            fan_speed = climate.get("baseFanSpeed")

        # Light settings (schedule strings like "06:00-18:00")
        light = settings.get("light", {})
        if light:
            day_schedule = light.get("day")
            night_schedule = light.get("night")

        # Tank/pump settings
        tank = settings.get("tank", {})
        if tank:
            waters = tank.get("waters", [])  # Pump schedules
            ph_setting = tank.get("ph", {})
            tank_capacity = tank.get("amountML")

            for water in waters:
                pump_num = water.get("pumpNum")
                schedules = water.get("waterSchedules", [])

        return True
    except Exception as e:
        logger.error(f"Error applying settings: {e}")
        return False
```

---

## Complete Integration Example

See `external_integrations/sample_integration.py` for a fully documented example that demonstrates:

- Self-registration with `register_capabilities()`
- Unified command interface with `execute_command()`
- Action handlers for all supported action types
- Settings application with `apply_settings()`
- Background polling tasks
- Error handling patterns

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
- Sample template: `external_integrations/sample_integration.py`
- Config schemas: `app/schemas/config_schemas.py`
- Device registry: `app/registry.py`

## Need Help?

Check the logs first - most issues are logged with helpful error messages. For additional support, reach out to the GrowAssistant community.

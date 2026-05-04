# Integration Development Guide

This guide provides details on how to create custom integrations for the GrowAssistant Bridge. The modular architecture allows for easy extension with new device types and communication protocols.

## Integration Architecture

The system is built around a **self-registering, Home Assistant-style modular architecture**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GrowAssistant Bridge                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                   Integration Discovery                      │    │
│  │  discover_integrations() → @register_integration decorator   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                               │                                      │
│         ┌─────────────────────┼─────────────────────┐               │
│         ▼                     ▼                     ▼               │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐        │
│  │    MQTT      │     │    GPIO      │     │   Custom     │        │
│  │ Integration  │     │ Integration  │     │ Integration  │        │
│  │              │     │              │     │              │        │
│  │ CONFIG_SCHEMA│     │ CONFIG_SCHEMA│     │ CONFIG_SCHEMA│        │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘        │
│         │                    │                    │                 │
│         └────────────────────┼────────────────────┘                 │
│                              ▼                                      │
│                    ┌──────────────────┐                             │
│                    │  Device Registry │                             │
│                    │  (domain.name)   │                             │
│                    │                  │                             │
│                    │ mqtt.temperature │                             │
│                    │ gpio.pump1       │                             │
│                    │ custom.sensor1   │                             │
│                    └────────┬─────────┘                             │
│                             │                                       │
│                             ▼                                       │
│                    ┌──────────────────┐                             │
│                    │    API Client    │                             │
│                    └──────────────────┘                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Integration Base Class | `app/integrations/__init__.py` | Abstract base class for all integrations |
| Device Registry | `app/registry.py` | Central registry with domain-qualified entity IDs |
| Config Schemas | `app/schemas/config_schemas.py` | Pydantic models for config validation |
| Dynamic Module Loading | `app/integrations/__init__.py` | Auto-discovers and loads integration modules |

---

## Creating a New Integration

### Step 1: Choose Your Location

**Built-in integrations** (shipped with the application):
```
app/integrations/my_integration/
├── __init__.py  # Contains your integration class
└── (other files)
```

**External integrations** (user-created):
```
external_integrations/
└── my_integration.py
```

### Step 2: Create the Integration Class

```python
"""My Custom Integration."""
import logging
from typing import Any, Dict, Generator, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from app.integrations import Integration, register_integration
from app.api_types import LogType, ActionType, ProblemType, ProblemStatus

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


# Step 2a: Define config schema (optional but recommended)
class MyDeviceConfig(BaseModel):
    name: str
    type: str
    address: Optional[str] = None

class MyIntegrationConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = Field(default=8080, ge=1, le=65535)
    devices: Dict[str, MyDeviceConfig] = {}


@register_integration
class MyIntegration(Integration):
    """Integration for my custom devices."""

    # Step 2b: Set config schema for validation
    CONFIG_SCHEMA = MyIntegrationConfig

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)  # Validates config if CONFIG_SCHEMA is set

        if not self.config.get("enabled", False):
            logger.info("My Integration is disabled")
            return

        # Access validated config
        self.host = self.config.get("host", "localhost")
        self.port = self.config.get("port", 8080)
        self.devices = {}

        # Parse device configurations
        for device_id, device_config in self.config.get("devices", {}).items():
            if isinstance(device_config, dict):
                self.devices[device_config.get("name")] = device_config

        logger.info(f"My Integration initialized with {len(self.devices)} devices")

    # Step 2c: Override get_config_key() for custom config key
    @classmethod
    def get_config_key(cls) -> str:
        """Return 'my' to match config.yaml section 'integrations.my:'"""
        return "my"

    async def connect(self) -> bool:
        """Establish connection to devices."""
        if not self.config.get("enabled", False):
            return False

        try:
            # Connect to your device/service
            # Example: self.client = await connect_to_service(self.host, self.port)

            # Register action handlers
            self.register_action_handler(ActionType.TEMPERATURE, self._handle_temp_action)

            logger.info("My Integration connected")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send command to device."""
        device_name = data.get("device")
        value = data.get("value")

        if not device_name or value is None:
            return False

        try:
            # Send to your device
            logger.debug(f"Sent {value} to {device_name}")
            return True
        except Exception as e:
            logger.error(f"Send failed: {e}")
            return False

    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive data from devices."""
        for name, device in self.devices.items():
            # Read from your device
            value = 25.0  # Example

            # Log to API
            if device.get("type") == "temperature":
                self.log_data(LogType.TEMPERATURE, value)

            yield {"device": name, "type": device.get("type"), "value": value}

    async def get_device_data(self) -> Dict[str, Any]:
        """Return current state of all devices."""
        return {name: {"type": d.get("type"), "value": 0} for name, d in self.devices.items()}

    # Step 2d: Implement self-registration
    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register sensors and actuators with the registry."""
        sensor_types = {"temperature", "humidity", "ph", "ec"}

        for name, device in self.devices.items():
            device_type = device.get("type")
            domain = "my"  # Your integration's domain

            if device_type in sensor_types:
                registry.register_sensor(
                    sensor_name=name,
                    integration_name=self.name,
                    domain=domain,
                    device_type=device_type,
                )
            else:
                registry.register_actuator(
                    actuator_name=name,
                    integration_name=self.name,
                    domain=domain,
                    device_type=device_type,
                )

        logger.info(f"Registered {len(self.devices)} devices")

    # Step 2e: Implement unified command interface
    async def execute_command(
        self,
        target_id: str,
        action: str,
        payload: Dict[str, Any]
    ) -> bool:
        """Execute command on target device."""
        if target_id not in self.devices:
            logger.error(f"Unknown device: {target_id}")
            return False

        # Handle common actions
        if action.lower() in ("on", "off"):
            value = 1 if action.lower() == "on" else 0
        elif action.lower() == "set":
            value = payload.get("value")
        else:
            value = payload.get("value")

        return await self.send_data({"device": target_id, "value": value})

    async def _handle_temp_action(self, action_data: Dict[str, Any]) -> bool:
        """Handle temperature action from API."""
        action_id = action_data.get("id")
        value = float(action_data.get("value", 0))

        # Find temperature device and set value
        for name, device in self.devices.items():
            if device.get("type") == "temperature":
                success = await self.send_data({"device": name, "value": value})
                if success:
                    self.acknowledge_action(action_id, received=True, resolved=True)
                return success

        return False

    async def disconnect(self):
        """Clean up resources."""
        # Close connections, cancel tasks, etc.
        logger.info("My Integration disconnected")
```

### Step 3: Add Configuration

In `config.yaml`:

```yaml
integrations:
  my:  # Matches get_config_key() return value
    enabled: true
    host: "192.168.1.100"
    port: 8080
    devices:
      '0':
        name: temp_sensor
        type: temperature
      '1':
        name: main_pump
        type: pump
```

### Step 4: Test Your Integration

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from my_integration import MyIntegration


@pytest.fixture
def config():
    return {
        "enabled": True,
        "host": "localhost",
        "port": 8080,
        "devices": {
            "0": {"name": "test_device", "type": "temperature"}
        }
    }


@pytest.mark.asyncio
async def test_connect(config):
    integration = MyIntegration(config)
    result = await integration.connect()
    assert result is True


@pytest.mark.asyncio
async def test_send_data(config):
    integration = MyIntegration(config)
    await integration.connect()
    result = await integration.send_data({"device": "test_device", "value": 25.0})
    assert result is True


@pytest.mark.asyncio
async def test_execute_command(config):
    integration = MyIntegration(config)
    await integration.connect()
    result = await integration.execute_command("test_device", "set", {"value": 30.0})
    assert result is True
```

---

## Integration Base Class API

### Class Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `CONFIG_SCHEMA` | `Optional[Type[BaseModel]]` | Pydantic model for config validation |

### Instance Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `config` | `Dict[str, Any]` | Raw configuration dictionary |
| `name` | `str` | Integration class name |
| `validated_config` | `Optional[BaseModel]` | Validated config (if CONFIG_SCHEMA set) |

### Required Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `connect()` | `async () -> bool` | Establish connection, return True on success |
| `send_data(data)` | `async (Dict) -> bool` | Send data/command to device |
| `receive_data()` | `async () -> Generator` | Yield data from devices |
| `get_device_data()` | `async () -> Dict` | Return current state of all devices |

### Optional Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_config_key()` | `classmethod () -> str` | Return config key (default: class name without 'Integration', lowercase) |
| `register_capabilities(registry)` | `(DeviceRegistry) -> None` | Self-register sensors/actuators |
| `execute_command(target_id, action, payload)` | `async () -> bool` | Unified command interface |
| `disconnect()` | `async () -> None` | Clean up resources |
| `apply_settings(settings)` | `async (Dict) -> bool` | Apply settings from API |
| `handle_action(action_data)` | `async (Dict) -> bool` | Handle action from API |

### API Communication Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `log_data(log_type, value, log_date, device_id)` | `(LogType, value, ...) -> None` | Log sensor reading to API. `device_id` is the device's `entity_id` (`<domain>.<name>`); when present, the API uses it to update `lastSeen`. |
| `report_problem(problem_type, status, description, ...)` | `(...) -> None` | Report problem to API |
| `register_action_handler(action_type, handler)` | `(ActionType, Callable) -> None` | Register action handler |
| `acknowledge_action(action_id, received, resolved)` | `(str, bool, bool) -> None` | Acknowledge API action |

---

## Device Registry

### Entity ID Format

Devices are identified by domain-qualified entity IDs:

```
Format: domain.device_name

Examples:
  - mqtt.temperature
  - gpio.pump1
  - my_integration.sensor1
```

### Registration Methods

```python
from app.registry import registry, DeviceCategory

# Register sensor (convenience method)
registry.register_sensor(
    sensor_name="temp1",
    integration_name=self.name,
    domain="my_domain",
    device_type="temperature",
)

# Register actuator (convenience method)
registry.register_actuator(
    actuator_name="pump1",
    integration_name=self.name,
    domain="my_domain",
    device_type="pump",
)

# Full control registration
registry.register_device(
    name="smart_pump",
    domain="my_domain",
    device_type="dosing_pump",
    category=DeviceCategory.ACTUATOR,
    integration_name=self.name,
    capabilities=["dispense", "calibrate"],
    metadata={"max_flow": 100},
)
```

### Query Methods

```python
# Get device by entity ID
device = registry.get_device("mqtt.temperature")

# Find device by name (searches all domains)
device = registry.find_device("temperature")

# Get all devices in a domain
devices = registry.get_devices_by_domain("gpio")

# Get all devices of a type
pumps = registry.get_devices_by_type("pump")

# Get devices for an integration
my_devices = registry.get_devices_by_integration("MyIntegration")

# Get all devices
all_devices = registry.get_all_devices()
```

---

## Manifest computation and push pipeline

After the registry has devices in it, the bridge serializes the entire
device set as a **manifest** and POSTs it to the API. The wire format
is documented in [`bridge-protocol.md`](bridge-protocol.md) §5; this
section covers the mechanics from a plugin author's point of view.

### Registry change callbacks

Every successful `register_device` (and `_remove_from_indexes`) fires
the registry's change callbacks (`app/registry.py:181-188`).
`api_client.py:107` registers `_on_registry_change` as one such
callback at startup. The callback's job is intentionally tiny:

1. If we're not on the asyncio loop, schedule via
   `run_coroutine_threadsafe` (`api_client.py:474-500`).
2. If we are, `asyncio.create_task(send_manifest())`.
3. If auth isn't ready or the loop is gone, no-op.

This means **registering a device synchronously triggers a manifest
push asynchronously**. As an integration author you don't need to do
anything special — just call `registry.register_sensor(...)` /
`register_actuator(...)` from `register_capabilities()` and the bridge
takes care of the rest.

### Hash algorithm (`compute_manifest_hash`)

The registry computes a SHA-256 over a deterministic JSON serialization
(`app/registry.py:192-218`). Concrete rules:

- Iterate devices in `entityId`-sorted order.
- For each device emit a JSON object with exactly seven keys:
  `entityId`, `domain`, `name`, `deviceType`, `category`,
  `integrationName`, `capabilities` (sorted).
- Use `json.dumps(payload, sort_keys=True, separators=(",", ":"))` —
  compact, no spaces.
- Join those per-device strings with `\n`, UTF-8 encode, SHA-256, hex.
- **`metadata` is excluded from the hash.** Tweaking `metadata` will
  not trigger a re-push on heartbeat drift.

The same string is computed on the API side; the heartbeat carries the
API's view of the hash and the bridge re-pushes when they disagree
(`_handle_heartbeat_event`, `api_client.py:858-869`).

### When pushes fire

Three triggers for `api_client.send_manifest()`:

1. **Startup** — once, after `_load_integrations()` completes
   (`main.py:117-122`). Skipped silently if not yet authenticated.
2. **Registry change** — every register/remove via the change-callback
   path described above.
3. **Hash drift on heartbeat** — the SSE consumer re-schedules a push.

`send_manifest` is serialized by an internal `asyncio.Lock`
(`api_client.py:518, :523`) so concurrent triggers coalesce into
sequential pushes. The bridge writes the accepted `manifestVersion` and
content hash back to `config_store` only on a 2xx response — failed
pushes leave the local counter alone and the next trigger retries with
the same `next_version`.

---

## SSE consumer pipeline

The bridge holds a single long-lived SSE connection
(`GET /bridge/{id}/stream`). The parser is in `api_client.py:696-779`;
event dispatch is in `_handle_sse_event` (`api_client.py:762-779`).

There are four event types and one handler each:

| Event       | Handler                       | Effect on bridge state                                                                 |
|-------------|-------------------------------|----------------------------------------------------------------------------------------|
| `connected` | `_handle_connected_event`     | Logs the API's `configVersion`. No state change.                                       |
| `config`    | `_handle_config_event`        | Saves full snapshot to `config_store`, extracts settings dict, awaits `settings_callback`. |
| `heartbeat` | `_handle_heartbeat_event`     | Drift checks (`configVersion`, `manifestHash`); fetches/re-pushes on mismatch.         |
| `action`    | `_handle_action_event`        | Puts the raw payload onto `_command_queue` for `_command_execution_task` to drain.     |

### Settings fan-out

`_handle_config_event` builds a settings dict shaped like:

```python
{
    "rdh_mode": payload.get("rdhMode", False),
    "status":   payload.get("status", ""),
    "light":    payload.get("light", {}),
    "climate":  payload.get("climate", {}),
    "tank":     payload.get("tank", {}),
}
```

…and awaits `_settings_callback(settings)`. That callback is
`Application._apply_settings` (`main.py:228-239`), which in turn calls
`integration.apply_settings(settings)` on every loaded integration.
Integrations that don't override `apply_settings` raise
`NotImplementedError`, which `_apply_settings` swallows — there's no
penalty for not implementing it.

### Heartbeat drift handling

The heartbeat carries `configVersion` and (optionally) `manifestHash`.
The bridge's logic (`api_client.py:832-869`):

1. If `remote.configVersion != local.configVersion`: call
   `fetch_full_config()` (a synchronous `GET /bridge/{id}`) and re-run
   the same fan-out as a `config` event.
2. If `remote.manifestHash` is set and `!= stored hash`: schedule
   `send_manifest()` as a fire-and-forget task.

Both checks run on every heartbeat; a single heartbeat can trigger
either, both, or neither.

---

## Config store schema

`app/config_store.py` is a tiny SQLite wrapper used for everything the
bridge needs to remember across restarts. Database file:
`data/config.db` (configurable via `general.config_db_file`).

### Tables

`local_config` — generic `(key, value, version, updated_at)` table.

| Key                 | Value semantics                                                                 | Version field |
|---------------------|---------------------------------------------------------------------------------|---------------|
| `full`              | The most-recent `BridgeSpaceResp` JSON, exactly as received over SSE / GET.     | `configVersion` from the payload. |
| `manifest_version`  | The last `acceptedVersion` returned by the API (stringified int).               | Same int again. |
| `manifest_hash`     | SHA-256 hex of the last successfully-pushed manifest.                           | `0` (unused).   |
| `device_assignments`| The `deviceAssignments` list from the most-recent `config` event (JSON list). Display-only. | `configVersion` of the carrying event. |

`outbound_queue` — `(id, endpoint, payload, created_at)`. Currently
*defined* but not yet wired into the regular send path. Intended for
durable retry of writes that fail while the API is offline.

### Lifecycle

- `config_store.start()` runs in `Application.start` after
  authentication completes.
- `config_store.stop()` closes the DB on shutdown.
- The store is a `SingletonMeta` instance — import `config_store` from
  anywhere and you get the same connection.

### Why this matters for plugin authors

If you implement `apply_settings`, your integration may receive a
settings dict on bridge startup *before SSE has connected*, sourced
from this cache (`main.py:182-196`). Don't assume "first call =
freshly delivered" — treat every settings application as idempotent.

---

## Command pipeline

When the API wants to actuate a device, it pushes an `event: action`
over SSE. The bridge moves it through this pipeline:

```
SSE event:action
   │
   ▼
api_client._handle_action_event   (api_client.py:817-830)
   │   puts the JSON payload on api_client._command_queue
   ▼
Application._command_execution_task  (main.py:370-402)
   │   pops queue, validates ready-state, calls:
   ▼
Application._process_command       (main.py:404-451)
   │   reads targetType, targetId, action, payload
   │
   ├─ targetType == "sensor"   → registry.get_sensor_integration(targetId)
   └─ targetType == "actuator" → registry.get_actuator_integration(targetId)
       │
       ▼
integration.execute_command(target_id, action, payload)
       │
       ▼
api_client.send_command_result(command_id, success, message)
       │   POST /bridge/{id}/actions/{action_id}/result
       ▼
   API records outcome
```

Plugin author's contract:

- Override `execute_command(self, target_id, action, payload)` if your
  integration needs custom routing — e.g. you want to dispatch on
  `action` ("on"/"off"/"set") and read additional fields from
  `payload`. The base class wraps `send_data` for you, but most
  integrations override.
- `target_id` is always the fully-qualified `entity_id` for the new
  API. (Legacy code passed bare names; the new API SHOULD always send
  `entity_id`.)
- Return `True` for success, `False` for failure. The bridge translates
  this to the result POST. Raising is also fine — exceptions are caught
  in `_process_command` and converted to `success=false` with the
  exception message.

There is no built-in retry on action result delivery; if the result
POST fails, the API will time the action out on its own policy.

---

## Bridge web UI

`web/app.py` exposes a Flask UI for operator-facing tasks. Endpoints
relevant to plugin authors:

| Endpoint               | Returns                                                                                  |
|------------------------|------------------------------------------------------------------------------------------|
| `GET /api/integrations`| `[{name, type, status}, ...]` for each loaded integration. 202 if integrations are still loading; `[]` if none enabled. (`web/app.py:299-334`) |
| `GET /api/devices`     | `{entity_id: {<integration's get_device_data fields>, assigned_role, role_slot}, ...}` — collected by calling `integration.get_device_data()` on every integration with a 10 s timeout per call. (`web/app.py:355-456`) |
| `GET /api/device-types`| `{deviceType: [actions, ...]}` from the registry. (`web/app.py:258-271`) |
| `GET /api/config`      | The bridge's `config.yaml` (sensitive fields masked). (`web/app.py:475-491`) |

### `assigned_role` flow into `/api/devices`

`_attach_assigned_roles` (`web/app.py:398-432`) reads
`config_store.get_device_assignments()` and joins on `entityId`. The
result decorates each device entry with:

- `assigned_role`: a `GrowRole` string (e.g. `"WATER_PUMP"`,
  `"UNASSIGNED"`, `"IGNORED"`).
- `role_slot`: an int for `MULTIPLE`-cardinality roles, else `null`.

If `get_device_data()` returned an error-shaped dict (`{"error": ...}`)
for some integration, the role attachment is skipped for that entry.

This is the only place the bridge surfaces role assignments — they are
pure UI labels. **Command routing never consults this list**; the
bridge always routes by `entity_id` via the registry.

---

## Configuration Validation

### Defining Schemas

```python
from pydantic import BaseModel, Field
from typing import Dict, Optional, Literal
from enum import Enum

class PinDirection(str, Enum):
    IN = "IN"
    OUT = "OUT"

class DeviceConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: str
    address: Optional[str] = None

class MyIntegrationConfig(BaseModel):
    enabled: bool = False
    host: str = Field(default="localhost")
    port: int = Field(default=8080, ge=1, le=65535)
    timeout: float = Field(default=30.0, gt=0)
    devices: Dict[str, DeviceConfig] = {}

    class Config:
        extra = "forbid"  # Reject unknown fields
```

### Using Validated Config

```python
@register_integration
class MyIntegration(Integration):
    CONFIG_SCHEMA = MyIntegrationConfig

    def __init__(self, config):
        super().__init__(config)  # Validation happens here

        # Access typed config
        if self.validated_config:
            self.host = self.validated_config.host
            self.port = self.validated_config.port
            for device in self.validated_config.devices.values():
                print(f"Device: {device.name}, Type: {device.type}")
```

---

## API Data Format

### Log Types

```python
from app.api_types import LogType

# Available log types
LogType.TEMPERATURE    # Temperature readings
LogType.HUMIDITY       # Humidity readings
LogType.LIGHT          # Light level
LogType.PH             # pH value
LogType.EC             # Electrical conductivity
LogType.TANK_ML        # Tank water dispensed (set device_id to the dosing pump's entity_id)
LogType.SUPPLEMENT_ML  # Nutrient dosing (set device_id to the dosing pump's entity_id)
LogType.PH_ML          # pH adjustment (set device_id to the dosing pump's entity_id)
```

### Problem Types

```python
from app.api_types import ProblemType, ProblemStatus

# Problem types
ProblemType.TEMPERATURE
ProblemType.HUMIDITY
ProblemType.LIGHT
ProblemType.WATER
ProblemType.PH
ProblemType.EC

# Problem statuses
ProblemStatus.RANGE       # Value out of range
ProblemStatus.CONNECTION  # Connection issue
ProblemStatus.SENSOR      # Sensor failure
```

### Action Types

```python
from app.api_types import ActionType

# Available action types
ActionType.TEMPERATURE     # Set temperature
ActionType.HUMIDITY        # Set humidity
ActionType.LIGHT           # Light on/off
ActionType.FAN             # Fan control
ActionType.TANK_ML         # Tank water amount
ActionType.PH_VALUE        # Target pH
ActionType.PH_ML           # Dispense pH adjuster
ActionType.SUPPLEMENT_ML   # Dispense nutrients
```

---

## Best Practices

### 1. Configuration Validation

Always define a `CONFIG_SCHEMA` to catch configuration errors early:

```python
CONFIG_SCHEMA = MyIntegrationConfig
```

### 2. Domain-Qualified Registration

Use explicit domains to prevent naming collisions:

```python
registry.register_sensor("temp", self.name, domain="my_integration", device_type="temperature")
```

### 3. Error Handling

Wrap I/O operations in try-except blocks:

```python
async def send_data(self, data):
    try:
        # ... operation
        return True
    except ConnectionError as e:
        logger.error(f"Connection failed: {e}")
        return False
```

### 4. Resource Cleanup

Always implement `disconnect()` for proper cleanup:

```python
async def disconnect(self):
    if self._task:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
    if self._client:
        await self._client.close()
```

### 5. Logging

Use appropriate log levels:

```python
logger.debug("Detailed debugging info")
logger.info("Normal operation events")
logger.warning("Potential issues")
logger.error("Errors requiring attention")
```

### 6. Action Handlers

Register handlers in `connect()` and acknowledge actions:

```python
async def connect(self):
    self.register_action_handler(ActionType.TEMPERATURE, self._handle_temp)
    return True

async def _handle_temp(self, action_data):
    action_id = action_data.get("id")
    # ... handle action
    self.acknowledge_action(action_id, received=True, resolved=True)
    return True
```

---

## Advanced Features

### Background Tasks

```python
async def connect(self) -> bool:
    self._polling_task = asyncio.create_task(self._poll_devices())
    return True

async def _poll_devices(self):
    while True:
        try:
            # Poll your devices
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break

async def disconnect(self):
    if self._polling_task:
        self._polling_task.cancel()
        await asyncio.gather(self._polling_task, return_exceptions=True)
```

### Settings Updates

```python
async def apply_settings(self, settings: Dict[str, Any]) -> bool:
    """Called when API sends settings updates."""
    try:
        climate = settings.get("climate", {})
        if climate:
            temp = climate.get("temperature")
            humidity = climate.get("humidity")

        light = settings.get("light", {})
        if light:
            day = light.get("day")     # e.g., "06:00-18:00"
            night = light.get("night")

        tank = settings.get("tank", {})
        if tank:
            # The new API addresses dosing devices by entity_id; iterate
            # the bridge's registry rather than relying on positional
            # pump indices.
            for water in tank.get("waters", []):
                target_entity_id = water.get("entityId")  # e.g. "gpio.water_pump"
                schedules = water.get("waterSchedules", [])

        return True
    except Exception as e:
        logger.error(f"Settings error: {e}")
        return False
```

---

## See Also

- [Bridge Protocol Specification](bridge-protocol.md) - Wire-format contract between bridge and API
- [Custom Integrations Guide](custom_integrations.md) - Detailed integration development guide
- [Sample Integration](../external_integrations/sample_integration.py) - Complete working example
- [Config Schemas](../app/schemas/config_schemas.py) - Built-in Pydantic schemas
- [Device Registry](../app/registry.py) - Registry implementation

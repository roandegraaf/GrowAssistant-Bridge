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
| `log_data(log_type, value, log_date, pump_num)` | `(LogType, value, ...) -> None` | Log sensor reading to API |
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
LogType.TANK_ML        # Tank water level (include pump_num)
LogType.SUPPLEMENT_ML  # Nutrient dosing (include pump_num)
LogType.PH_ML          # pH adjustment (include pump_num)
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
            for water in tank.get("waters", []):
                pump_num = water.get("pumpNum")
                schedules = water.get("waterSchedules", [])

        return True
    except Exception as e:
        logger.error(f"Settings error: {e}")
        return False
```

---

## See Also

- [Custom Integrations Guide](custom_integrations.md) - Detailed integration development guide
- [Sample Integration](../external_integrations/sample_integration.py) - Complete working example
- [Config Schemas](../app/schemas/config_schemas.py) - Built-in Pydantic schemas
- [Device Registry](../app/registry.py) - Registry implementation

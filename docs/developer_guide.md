# Integration Development Guide

This guide provides details on how to create custom integrations for the GrowAssistant Bridge. The modular architecture allows for easy extension with new device types and communication protocols.

## Integration Architecture

The system is built around the following key components:

1. **Integration Base Class**: Abstract class that all integrations must inherit from
2. **Device Type Registry**: Central registry for mapping device types to integrations and actions
3. **Dynamic Module Loading**: System for auto-discovering and loading integration modules

## Creating a New Integration

### Step 1: Set Up the Integration Directory

Create a new directory under `app/integrations/` for your integration:

```
app/integrations/my_custom_integration/
```

### Step 2: Create the Integration Module

Create an `__init__.py` file in your integration directory with your integration class that inherits from the `Integration` base class:

```python
from app.integrations import Integration, register_integration

@register_integration
class MyCustomIntegration(Integration):
    """
    My custom integration that supports [description of what it does].
    """
    
    def __init__(self, config):
        super().__init__(config)
        # Initialize any instance variables here
        self.connection = None
        self.custom_config = self.config.get("integrations.my_custom_integration", {})
    
    async def connect(self):
        """
        Establish connection to the device or service.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            # Implement connection logic here
            self.connection = await self._establish_connection()
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect: {str(e)}")
            return False
    
    async def send_data(self, data):
        """
        Send data to the device or service.
        
        Args:
            data (dict): Data to send.
            
        Returns:
            bool: True if send successful, False otherwise.
        """
        try:
            # Implement data sending logic here
            return True
        except Exception as e:
            self.logger.error(f"Failed to send data: {str(e)}")
            return False
    
    async def receive_data(self):
        """
        Receive data from the device or service.
        
        Yields:
            dict: Data received from the device.
        """
        try:
            # Implement data receiving logic here
            # Use 'yield' to return data incrementally
            yield {"value": 42, "unit": "C"}
        except Exception as e:
            self.logger.error(f"Failed to receive data: {str(e)}")
```

### Step 3: Configuration

Add your integration's configuration to `config.yaml`:

```yaml
integrations:
  my_custom_integration:
    enabled: true
    parameter1: value1
    parameter2: value2
```

### Step 4: Register Device Types

Register your device types in your application startup code or in a separate function:

```python
def register_device_types(registry, integrations):
    # Find your integration instance
    my_integration = next((i for i in integrations if isinstance(i, MyCustomIntegration)), None)
    
    if my_integration:
        # Register device types with the registry
        registry.register_device_type(
            "my_sensor",
            my_integration,
            receive_actions=["read_temperature", "read_humidity"],
            send_actions=["set_limits", "calibrate"]
        )
```

## Integration Base Class API

The `Integration` base class provides the following methods and attributes:

### Methods to Implement

| Method | Description |
|--------|-------------|
| `connect()` | Establish a connection to the underlying hardware or service. |
| `send_data(data)` | Send data to the device or service. |
| `receive_data()` | Asynchronous generator that yields data from the device or service. |
| `get_device_data()` | Return the current state of all devices managed by this integration. |
| `apply_settings(settings)` | (Optional) Apply settings received from the API. Raises NotImplementedError by default. |
| `handle_action(action_data)` | (Optional) Handle actions requested by the API. Returns False by default. |
| `disconnect()` | Clean up resources when shutting down. |

### Available Attributes and Methods

| Attribute/Method | Description |
|------------------|-------------|
| `self.config` | Access to the application configuration. |
| `self.logger` | Logger for the integration. |
| `self.name` | Name of the integration (derived from class name). |

### Helper Methods for API Communication

The Integration base class provides convenient methods for communicating with the GrowAssistant API:

| Method | Description |
|--------|-------------|
| `log_data(log_type, value, log_date=None, pump_num=None)` | Send a data log to the API. |
| `report_problem(problem_type, status, description, priority, ...)` | Report a problem to the API. |
| `register_action_handler(action_type, handler)` | Register a handler for a specific action type. |
| `acknowledge_action(action_id, received, resolved)` | Acknowledge an action from the API. |

Example usage:

```python
from app.api_types import LogType, ProblemType, ProblemStatus, ActionType

# Log data
self.log_data(LogType.TEMPERATURE, 25.5)
self.log_data(LogType.TANK_ML, 500, pump_num=1)  # 500ml from pump 1

# Report a problem
self.report_problem(
    ProblemType.TEMPERATURE,
    ProblemStatus.CONNECTION,
    "Temperature sensor not responding",
    priority=80,
    user_can_resolve=False
)

# Register action handlers (typically in connect method)
self.register_action_handler(ActionType.TEMPERATURE, self.handle_temperature_action)
```

## Testing Your Integration

Create a test file in the `tests/` directory to verify your integration works correctly:

```python
import pytest
from unittest.mock import MagicMock, patch
from app.integrations.my_custom_integration import MyCustomIntegration

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.return_value = {
        "parameter1": "test_value1",
        "parameter2": "test_value2"
    }
    return config

@pytest.mark.asyncio
async def test_my_custom_integration(mock_config):
    integration = MyCustomIntegration(mock_config)
    
    # Test connection
    with patch.object(integration, '_establish_connection', return_value=True):
        assert await integration.connect() is True
    
    # Test sending data
    test_data = {"command": "test"}
    assert await integration.send_data(test_data) is True
    
    # Test receiving data
    data_items = []
    async for data in integration.receive_data():
        data_items.append(data)
    
    assert len(data_items) > 0
    assert "value" in data_items[0]
```

## Advanced Features

### Integration Lifecycle Events

You can implement these optional methods to handle lifecycle events:

```python
async def disconnect(self):
    """Called when the application stops."""
    # Clean up resources
    if self.connection:
        await self.connection.close()

async def apply_settings(self, settings: Dict[str, Any]) -> bool:
    """Called when settings are received from the API.

    The settings dict contains:
    - rdh_mode: bool
    - status: str
    - light: dict with 'day' and 'night' schedules
    - climate: dict with 'temperature', 'humidity', 'baseFanSpeed'
    - tank: dict with 'waters', 'ph', 'amountML'
    """
    try:
        # Apply climate settings
        climate = settings.get("climate", {})
        if climate:
            temp = climate.get("temperature")
            if temp is not None:
                await self._set_target_temperature(temp)

        # Apply light settings
        light = settings.get("light", {})
        if light:
            day_schedule = light.get("day")
            night_schedule = light.get("night")
            # Configure light schedules

        # Apply tank/pump settings
        tank = settings.get("tank", {})
        if tank:
            waters = tank.get("waters", [])
            for water in waters:
                pump_num = water.get("pumpNum")
                schedules = water.get("waterSchedules", [])
                # Configure pump schedules

        return True
    except Exception as e:
        logger.error(f"Error applying settings: {e}")
        return False
```

If your integration doesn't need to respond to settings updates, don't implement this method (it will raise NotImplementedError by default, which is handled gracefully by the system).

### Error Handling and Retries

Implement robust error handling in your integrations:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

class MyCustomIntegration(Integration):
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def _api_call_with_retry(self, *args, **kwargs):
        # Implement API call that will retry on failure
        pass
```

## Best Practices

1. **Error Handling**: Always wrap I/O operations in try-except blocks with appropriate logging.
2. **Resource Management**: Properly clean up resources in `disconnect()` method.
3. **Configuration**: Make your integration configurable via the `config.yaml` file.
4. **Logging**: Use the provided logger (`self.logger`) for all messages.
5. **Documentation**: Document your integration's capabilities and configuration options.
6. **Testing**: Write comprehensive tests for your integration.
7. **Data Logging**: Use `log_data()` method for all sensor readings. Include `pump_num` parameter for water/nutrient-related data.
8. **Problem Reporting**: While the system automatically detects common issues (out-of-range values, sensor failures), you can report integration-specific problems using `report_problem()`.
9. **Settings Support**: If your integration controls devices that can be remotely configured, implement `apply_settings()` to respond to API configuration updates.
10. **Action Handling**: Register action handlers in your `connect()` method and implement proper error handling in handler functions. 
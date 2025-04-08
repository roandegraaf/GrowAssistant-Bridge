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

### Available Attributes and Methods

| Attribute/Method | Description |
|------------------|-------------|
| `self.config` | Access to the application configuration. |
| `self.logger` | Logger for the integration. |
| `self.name` | Name of the integration (derived from class name). |

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
async def start(self):
    """Called when the application starts."""
    pass

async def stop(self):
    """Called when the application stops."""
    # Clean up resources
    if self.connection:
        await self.connection.close()
```

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
2. **Resource Management**: Properly clean up resources in `stop()` method.
3. **Configuration**: Make your integration configurable via the `config.yaml` file.
4. **Logging**: Use the provided logger (`self.logger`) for all messages.
5. **Documentation**: Document your integration's capabilities and configuration options.
6. **Testing**: Write comprehensive tests for your integration. 
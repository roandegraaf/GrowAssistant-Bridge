# External Integrations for GrowAssistant Bridge

This directory is where you can add your own custom integrations for the GrowAssistant Bridge.

## How to Create a Custom Integration

1. **Start by copying the sample integration**:
   - Use `sample_integration.py` as a template for your new integration
   - Rename the file to something descriptive of your integration (e.g., `my_sensor.py`)

2. **Implement your integration class**:
   - Your class must inherit from `Integration` base class
   - You must implement all required methods:
     - `connect()`: Establish connection to your device/service
     - `send_data()`: Send commands/data to your device/service
     - `receive_data()`: Receive data from your device/service
     - `get_device_data()`: Get current state for all devices
     - `disconnect()`: Clean up resources when integration is stopped
   - Optional methods you can implement:
     - `apply_settings(settings)`: Receive and apply settings from the API
     - `handle_action(action_data)`: Handle actions requested by the API
   - Decorate your class with `@register_integration`

3. **Create configuration for your integration**:
   - Add a section for your integration in `config.yaml`
   - See `sample_config.yaml` for an example configuration

## Installation

1. Save your integration Python file in this directory.
2. Add configuration for your integration in the main `config.yaml` file.
3. Restart the GrowAssistant Bridge application.

## Tips for Development

- **Integration Name**: The integration name in the configuration should match the Python module name (without the .py extension).
- **Logging**: Use the `logger` to provide useful debug and error messages.
- **Error Handling**: Add proper error handling for robustness.
- **Dependencies**: If your integration requires external libraries, make sure to include them in your requirements.
- **Testing**: Test your integration thoroughly before deploying to production.
- **API Communication**: Use the helper methods provided by the Integration base class:
  - `self.log_data(LogType, value, pump_num=None)`: Send sensor readings to the API
  - `self.report_problem(ProblemType, status, description, ...)`: Report issues to the API
  - `self.register_action_handler(ActionType, handler)`: Register handlers for API actions
  - `self.acknowledge_action(action_id, received, resolved)`: Acknowledge actions from the API
- **Automatic Problem Detection**: The system automatically detects common problems (out-of-range values, sensor failures) from your data logs.

## Example Integration Structure

```python
from app.integrations import Integration, register_integration

@register_integration
class MyIntegration(Integration):
    def __init__(self, config):
        super().__init__(config)
        # Initialize your integration
        
    async def connect(self):
        # Connect to your device/service
        return True
        
    async def send_data(self, data):
        # Send data to your device/service
        return True
        
    async def receive_data(self):
        # Yield data from your device/service
        yield {"device": "my_device", "value": 123}
        
    async def get_device_data(self):
        # Return current state of all devices
        return {"my_device": {"value": 123}}

    async def apply_settings(self, settings):
        # Optional: Apply settings from API
        # Implement only if your integration needs to respond to settings
        climate = settings.get("climate", {})
        if climate.get("temperature"):
            await self._set_target_temperature(climate["temperature"])
        return True

    async def disconnect(self):
        # Clean up resources
        pass
```

## Example Configuration

```yaml
integrations:
  myintegration:  # Module name (without .py)
    enabled: true
    # Add your custom configuration parameters here
    devices:
      '0':
        name: my_device
        type: temperature
```

## Additional Resources

- Check the existing built-in integrations in the `app/integrations/` directory for more examples.
- See `sample_integration.py` in this directory for a complete, documented example.
- Read the [Custom Integrations Guide](../docs/custom_integrations.md) for detailed documentation on:
  - API data format (dataLogs, problems, actions, settings)
  - Problem detection and reporting
  - Action handling
  - Settings application
  - Best practices and troubleshooting 
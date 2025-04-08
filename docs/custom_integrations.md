# Developing Custom Integrations for GrowAssistant Bridge

This guide will help you develop custom integrations for the GrowAssistant Bridge, allowing you to integrate various devices and services with the GrowAssistant platform.

## Table of Contents

1. [Overview](#overview)
2. [Integration Basics](#integration-basics)
3. [Creating Your First Integration](#creating-your-first-integration)
4. [Integration Structure](#integration-structure)
5. [Configuration](#configuration)
6. [Testing Your Integration](#testing-your-integration)
7. [Best Practices](#best-practices)
8. [Troubleshooting](#troubleshooting)

## Overview

GrowAssistant Bridge is designed with a pluggable integration system, allowing anyone with basic Python knowledge to add support for new devices, sensors, or services. Once integrated, these new devices can be monitored and controlled through the GrowAssistant platform just like the built-in integrations.

## Integration Basics

Every integration in GrowAssistant Bridge:

1. **Inherits** from the base `Integration` class
2. **Implements** required methods (`connect`, `send_data`, `receive_data`, `get_device_data`, `disconnect`)
3. **Registers** itself with the system using the `@register_integration` decorator
4. **Accepts** configuration from the config.yaml file
5. **Reports** data to the main application, which forwards it to the GrowAssistant platform

## Creating Your First Integration

### Step 1: Set Up the Development Environment

1. Copy the sample integration template from the `external_integrations` directory:
   ```bash
   cp external_integrations/sample_integration.py external_integrations/my_integration.py
   ```

2. Review the sample configuration:
   ```
   external_integrations/sample_config.yaml
   ```

### Step 2: Implement Your Integration

1. Modify `my_integration.py` to implement your specific integration
2. Use the sample template as a guide
3. Implement all required methods

### Step 3: Configure Your Integration

1. Add configuration for your integration in `config.yaml`
2. Follow the structure in `sample_config.yaml`

### Step 4: Test Your Integration

1. Restart GrowAssistant Bridge
2. Check the logs to ensure your integration loads correctly
3. Verify that data is being sent to the GrowAssistant platform

## Integration Structure

Every integration must implement these methods:

### `__init__(self, config)`

- Initializes the integration with configuration
- Parses and validates configuration
- Sets up internal data structures

### `connect() -> bool`

- Establishes connection to the device/service
- Returns True if successful, False otherwise
- May start background tasks for ongoing operations

### `send_data(data) -> bool`

- Sends commands or data to the device/service
- Returns True if successful, False otherwise
- The `data` parameter varies based on device type

### `receive_data() -> Generator`

- Yields data received from the device/service
- Called periodically by the main application
- Data should have standard format for the system

### `get_device_data() -> dict`

- Returns current state of all devices managed by this integration
- Used for status queries

### `disconnect()`

- Cleans up resources when the application stops
- Cancels any background tasks
- Closes connections to hardware/services

## Configuration

Your integration's configuration should be defined in the main `config.yaml` file under the `integrations` section:

```yaml
integrations:
  myintegration:  # Should match module name (without .py extension)
    enabled: true
    # Custom parameters for your integration
    update_interval: 60
    devices:
      '0':
        name: my_device
        type: temperature
        # Device-specific parameters
```

The system will pass this configuration to your integration's `__init__` method.

## Best Practices

1. **Error Handling**: Always include proper error handling in your integration. Use try/except blocks around I/O operations and log meaningful error messages.

2. **Logging**: Use the logger to provide useful diagnostic information. Log at appropriate levels (debug, info, warning, error).

3. **Configuration Validation**: Always validate configuration parameters to avoid runtime errors.

4. **Clean Shutdown**: Implement proper resource cleanup in the `disconnect` method.

5. **Documentation**: Include docstrings in your code and clear configuration examples.

6. **Background Tasks**: If your integration needs to poll or maintain a connection, use asyncio tasks properly.

7. **Standards**: Follow the data format standards used by the system for consistent behavior.

## Troubleshooting

### Integration Not Loading

1. Check that your Python file is in the `external_integrations` directory
2. Verify that the class name is correct and has the `@register_integration` decorator
3. Look for import errors in the logs

### Integration Loads But No Data Shows Up

1. Verify your configuration in config.yaml
2. Check that your `receive_data` method is yielding data properly
3. Look for errors in the logs

### Commands Not Working

1. Verify your `send_data` method implementation
2. Check that device names match between configuration and code
3. Ensure that your device is properly registered as an actuator

## Example Integrations

For more examples, look at:

1. The built-in integrations in `app/integrations/`
2. The DHT sensor example in `external_integrations/dht_sensor.py`

## Need Help?

If you need assistance with your integration, check the documentation or reach out to the GrowAssistant community for support. 
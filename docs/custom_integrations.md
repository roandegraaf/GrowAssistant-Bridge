# Developing Custom Integrations for GrowAssistant Bridge

This guide will help you develop custom integrations for the GrowAssistant Bridge, allowing you to integrate various devices and services with the GrowAssistant platform.

## Table of Contents

1. [Overview](#overview)
2. [Integration Basics](#integration-basics)
3. [Creating Your First Integration](#creating-your-first-integration)
4. [Integration Structure](#integration-structure)
5. [API Data Format](#api-data-format)
6. [Configuration](#configuration)
7. [Testing Your Integration](#testing-your-integration)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)

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

### `apply_settings(settings) -> bool` (Optional)

- Receives and applies settings from the API
- Called automatically when the API returns configuration updates
- Allows integrations to respond to remote settings changes
- Should return True if successful, False otherwise
- Raises NotImplementedError if the integration doesn't support settings (default behavior)

## API Data Format

GrowAssistant Bridge communicates with the GrowAssistant API using a specific data format that includes three main components:

### Data Format Overview

1. **Data Logs**: Sensor readings and other data points
2. **Problems**: Issues that need attention
3. **Actions**: Responses to commands sent from the API

### Data Logs

Data logs represent sensor readings or other data points collected by your integration. Each log entry includes:

- `logDate`: ISO-formatted timestamp
- `logType`: Type of data (e.g., TEMPERATURE, HUMIDITY, WATER)
- `value`: The actual value
- `pumpNum`: (Optional) Pump number for water/nutrient-related logs

Example of sending a data log:

```python
# In your integration class:
self.log_data(LogType.TEMPERATURE, 25.5)
self.log_data(LogType.HUMIDITY, 65)

# For pump-related data, include the pump number
self.log_data(LogType.TANK_ML, 500, pump_num=1)  # 500ml from pump 1
```

**Automatic Problem Detection**: The system automatically analyzes data logs and detects potential issues such as:
- Sensor failures (error values, null readings)
- Out-of-range values (temperature, humidity, pH)
- Connection issues

When problems are detected, they're automatically reported to the API.

### Problems

Problems represent issues that require attention, such as sensor readings outside expected ranges or hardware failures. Each problem includes:

- `id`: Unique identifier
- `priority`: Importance level (0-100)
- `description`: Human-readable description
- `type`: Problem category (CONNECTION, SENSOR, RANGE, etc.)
- `status`: System affected (TEMPERATURE, HUMIDITY, etc.)
- `userCanResolve`: Whether the user can fix this
- `resolved`: Whether the problem is already resolved

Example of reporting a problem:

```python
# In your integration class:
self.report_problem(
    ProblemType.TEMPERATURE,
    ProblemStatus.RANGE,
    "Temperature out of range: 35.2°C",
    priority=70,
    user_can_resolve=True
)
```

### Actions

Actions represent commands from the API that your integration should execute. Each action includes:

- `id`: Unique identifier
- `type`: Action type (TEMPERATURE, HUMIDITY, etc.)
- `value`: Target value
- `pumpNumber`: Pump number (for water/nutrient actions)

Your integration should handle actions by implementing the `handle_action` method and registering handlers for specific action types.

Example of handling actions:

```python
# In your integration class:
async def connect(self):
    # Register handlers during initialization
    self.register_action_handler(ActionType.TEMPERATURE, self.handle_temperature_action)
    self.register_action_handler(ActionType.LIGHT, self.handle_light_action)
    self.register_action_handler(ActionType.SUPPLEMENT_ML, self.handle_supplement_action)
    # ... other handlers

async def handle_temperature_action(self, action_data):
    # Extract values from action_data
    value = float(action_data.get("value", 0))
    action_id = action_data.get("id")

    # Perform the action (e.g., set temperature)
    success = await self._set_temperature(value)

    # Acknowledge completion
    if success:
        self.acknowledge_action(action_id, received=True, resolved=True)

    return success

async def handle_light_action(self, action_data):
    # Light control is typically on/off, not dimming
    value = action_data.get("value")  # "on" or "off"
    action_id = action_data.get("id")

    # Convert to hardware format
    if value == "on":
        await self._turn_light_on()
    elif value == "off":
        await self._turn_light_off()

    return True

async def handle_supplement_action(self, action_data):
    # Pump actions include pumpNumber
    value = float(action_data.get("value", 0))  # ML to dispense
    pump_number = action_data.get("pumpNumber")  # Which pump to use
    action_id = action_data.get("id")

    # Dispense nutrients from the specified pump
    success = await self._dispense_supplement(pump_number, value)

    # Log the action with pump number for tracking
    if success:
        self.log_data(LogType.NUTRITION, value, pump_num=pump_number)

    return success
```

### Action Types

The following action types are supported by the API:

- `TEMPERATURE`: Set target temperature (value in °C)
- `HUMIDITY`: Set target humidity (value in %)
- `LIGHT`: Light on/off control (value typically "on" or "off")
- `FAN`: Fan speed control (value as percentage or speed level)
- `TANK_ML`: Set tank water capacity (value in milliliters)
- `PH_VALUE`: Set target pH value (value as pH level, includes pumpNumber)
- `PH_ML`: Dispense pH adjuster (value in milliliters, includes pumpNumber)
- `SUPPLEMENT_ML`: Dispense nutrient supplement (value in milliliters, includes pumpNumber)

**Note about pump actions**: Actions for pH and supplements include a `pumpNumber` field to specify which dosing pump to use. Your integration should extract this from `action_data.get("pumpNumber")` and use it to control the correct pump.

### Settings Updates

The API sends configuration updates with each response, including:

- `rdhMode`: Current RDH (Run Dry Harvest) mode status (boolean)
- `status`: Current environment status (string)
- `light`: Light schedules with day/night settings (strings, e.g., "06:00-18:00" or "auto")
- `climate`: Target temperature (°C), humidity (%), and fan speed (baseFanSpeed)
- `tank`: Water schedules for pumps (with pumpNum, waterAmountML, startTime, endTime), pH settings (pH value and pumpNum), and tank capacity (amountML)

Your integration can receive and apply these settings by implementing the `apply_settings` method:

```python
async def apply_settings(self, settings: Dict[str, Any]) -> bool:
    """Apply settings received from the API."""
    try:
        # Apply climate settings
        climate = settings.get("climate", {})
        if climate:
            temp = climate.get("temperature")
            humidity = climate.get("humidity")
            fan_speed = climate.get("baseFanSpeed")

            # Update your devices with new settings
            if temp is not None:
                await self._set_target_temperature(temp)
            if humidity is not None:
                await self._set_target_humidity(humidity)

        # Apply light settings
        # Light schedules are strings like "06:00-18:00" or "auto"
        light = settings.get("light", {})
        if light:
            day_schedule = light.get("day")    # e.g., "06:00-18:00"
            night_schedule = light.get("night")  # e.g., "off"

            # Parse the schedule strings and set up timers
            # Example: "06:00-18:00" means lights on at 6am, off at 6pm
            if day_schedule:
                # Parse and apply day schedule
                logger.info(f"Configuring day light schedule: {day_schedule}")
                # You would implement timer/cron logic here

        # Apply tank/water settings
        tank = settings.get("tank", {})
        if tank:
            waters = tank.get("waters", [])  # Array of pump configurations
            ph_setting = tank.get("ph", {})  # pH target and pump
            tank_capacity = tank.get("amountML")  # Tank capacity

            # Configure each pump with its schedules
            for water in waters:
                pump_num = water.get("pumpNum")
                schedules = water.get("waterSchedules", [])

                # Each schedule defines when and how much to water
                for schedule in schedules:
                    amount_ml = schedule.get("waterAmountML")
                    start_time = schedule.get("startTime")
                    end_time = schedule.get("endTime")
                    schedule_type = schedule.get("scheduleType")

                    # Set up timer for this watering schedule
                    logger.info(f"Pump {pump_num}: {amount_ml}ML from {start_time} to {end_time}")
                    # You would implement timer/cron logic here

            # Configure pH controller
            if ph_setting:
                target_ph = ph_setting.get("ph")
                ph_pump = ph_setting.get("pumpNum")
                logger.info(f"pH target: {target_ph} using pump {ph_pump}")

        return True
    except Exception as e:
        logger.error(f"Error applying settings: {e}")
        return False
```

If your integration doesn't need to respond to settings (e.g., read-only sensors), you don't need to implement this method.

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

8. **Log Appropriate Data**: Send data logs for all relevant sensor readings. Include `pump_num` when logging water/nutrient data.

9. **Report Problems Promptly**: Use the problem reporting system to alert users of issues. Note that common problems (out-of-range values, sensor failures) are automatically detected.

10. **Handle Actions Robustly**: Implement error handling in action handlers.

11. **Acknowledge Receipt**: Always acknowledge actions even if you can't complete them.

12. **Provide Clear Descriptions**: Use clear, actionable descriptions for problems.

13. **Implement Settings When Needed**: If your integration controls devices that can be configured remotely (lights, climate, pumps), implement the `apply_settings` method to respond to configuration updates from the API.

14. **Handle Settings Gracefully**: If implementing `apply_settings`, handle partial or missing settings data gracefully - apply what's available and log any issues.

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

### Actions Not Being Processed

1. Check that you've registered action handlers in your `connect` method
2. Verify that your action handler function returns the correct result
3. Look for errors in the action handler logs

### Settings Not Being Applied

1. Verify that you've implemented the `apply_settings` method in your integration
2. Check the logs for errors in the settings callback
3. Ensure your integration doesn't raise NotImplementedError from `apply_settings`
4. Verify that the API is sending settings data in the response

## Example Integrations

For more examples, look at:

1. The built-in integrations in `app/integrations/`
2. The DHT sensor example in `external_integrations/dht_sensor.py`

## Need Help?

If you need assistance with your integration, check the documentation or reach out to the GrowAssistant community for support. 
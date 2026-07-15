# GrowAssistant Bridge

A bridge application to connect various sensors and controllers for cannabis growing environments to the GrowAssistant API. This application provides a unified interface for data collection, control, and monitoring.

## Features

- Modular design with support for multiple integration types:
  - GPIO pins (for Raspberry Pi and similar devices)
  - MQTT messaging
  - HTTP endpoints
  - Serial communication
- Data collection with persistence
- Regular API communication with automatic data transmission
- Automatic problem detection (sensor failures, out-of-range values)
- Bidirectional communication with API for receiving settings and commands
- Command handling for device control
- Web interface for monitoring and manual control
- Extensible architecture for adding new device types and integrations
- Detailed logging with individual log files for API data transmissions

## Documentation

- [`docs/bridge-protocol.md`](docs/bridge-protocol.md) — wire-format
  contract between bridge and API. Read this if you are building or
  modifying an API server that talks to the bridge.
- [`docs/developer_guide.md`](docs/developer_guide.md) — internal
  architecture, integration plugin authoring guide, and pipeline
  details. Read this if you are writing a new integration or hacking
  on bridge internals.

## Requirements

- Raspberry Pi (3B+ or newer recommended)
- Raspberry Pi OS (64-bit recommended)
- Python 3.9+
- Node.js 18+ (for building frontend assets, optional)
- Dependencies listed in `requirements.txt`
- [go2rtc](https://github.com/AlexxIT/go2rtc) (optional) — only for the
  `camera` integration's WebRTC streaming. It is an **external binary, not a
  pip dependency**: install it separately and put it on `PATH` (or set
  `integrations.camera.go2rtc_binary` to its full path). The bridge spawns and
  supervises go2rtc and relays SDP between the app and go2rtc, which acts as
  the actual WebRTC media peer. `ffmpeg` is required if you use an
  `ffmpeg:`/`exec:ffmpeg ...` stream source.

## Quick Start (Recommended)

Install GrowAssistant Bridge with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/roandegraaf/GrowAssistant-Bridge/main/install.sh | bash
```

This will automatically install system packages, clone the repository to `~/GrowAssistant-Bridge`, and launch the interactive setup.

The setup script will guide you through:
- Installing Python dependencies
- Selecting integrations (GPIO, MQTT, HTTP, Serial)
- Configuring the web interface (username, password, port)
- Setting up auto-start on boot (optional)
- Starting the application

After setup, you'll see your dashboard URLs:
```
╔════════════════════════════════════════════════════════════╗
║        ✓ GrowAssistant Bridge is running!                  ║
╠════════════════════════════════════════════════════════════╣
║  Access your dashboard at:                                 ║
║                                                            ║
║  • http://192.168.1.45:5000                                ║
║  • http://raspberrypi:5000                                 ║
║  • http://raspberrypi.local:5000                           ║
╚════════════════════════════════════════════════════════════╝
```

## Manual Installation

If you prefer manual installation or need more control, follow these steps:

### 1. Initial Setup

1. Install Raspberry Pi OS:
   - Download the latest Raspberry Pi OS from [raspberrypi.org](https://www.raspberrypi.org/software/)
   - Use Raspberry Pi Imager to write the OS to your SD card
   - Enable SSH during initial setup if you plan to access the Pi remotely

2. Update your Raspberry Pi:
   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```

3. Install required system packages:
   ```bash
   sudo apt install -y python3-pip python3-venv git nodejs npm
   ```

### 2. Clone and Setup the Application

1. Clone the repository:
   ```bash
   git clone https://github.com/roandegraaf/GrowAssistant-Bridge.git
   cd GrowAssistant-Bridge
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Build frontend assets (requires Node.js):
   ```bash
   npm install
   npm run build:css
   ```

### 3. GPIO Setup (if using GPIO features)

1. Enable GPIO access:
   ```bash
   sudo usermod -a -G gpio $USER
   sudo usermod -a -G i2c $USER  # If using I2C devices
   ```

2. Enable required interfaces:
   - Run `sudo raspi-config`
   - Navigate to "Interface Options"
   - Enable GPIO, I2C, and any other required interfaces
   - Reboot when prompted

### 4. Configuration

1. Copy the example configuration file:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Edit the configuration file:
   ```bash
   nano config.yaml
   ```

3. Configure the following sections in `config.yaml`:

   **API Configuration:**
   ```yaml
   api:
     url: https://api.growassistant.app/prod  # The GrowAssistant API URL
     batch_size: 100                          # Number of data points to send in one batch
     poll_interval: 5                         # How often to check for new commands (seconds)
     transmission_interval: 10                # How often to send data to the API (seconds)
     connection_timeout: 300                  # Timeout for initial connection (seconds)
     log_values: true                         # Enable detailed API value logging
     retry_max_attempts: 5                    # Max retry attempts for failed requests
     retry_min_backoff: 1                     # Minimum backoff between retries (seconds)
     retry_max_backoff: 60                    # Maximum backoff between retries (seconds)
   ```

   **General Settings:**
   ```yaml
   general:
     collection_interval: 10                  # How often to collect data from sensors (seconds)
     data_dir: data                           # Directory for persistent data
     log_file: logs/app.log                   # Main application log file
     log_level: INFO                          # Logging level (DEBUG, INFO, WARNING, ERROR)
     api_logs_dir: logs/api_values            # Directory for API value logs
     external_integrations_dir: external_integrations  # Directory for custom integrations
   ```

   **Queue Settings:**
   ```yaml
   queue:
     max_queue_size: 10000                    # Maximum number of items in queue
     flush_interval: 300                      # How often to flush queue to disk (seconds)
     persistence_enabled: true                # Enable queue persistence
     persistence_file: data/queue.db          # Queue database file
   ```

   **Web Interface Settings:**
   ```yaml
   web:
     enabled: true
     host: 127.0.0.1                          # Use 0.0.0.0 to allow external access
     port: 5000
     debug: false                             # Enable debug mode (not for production)
     auth_enabled: true
     username: admin
     password_hash: ''                        # Set via web interface on first setup
     secret_key: ''                           # Auto-generated on first run if empty
     ssl_enabled: false                       # Enable for HTTPS
     ssl_cert: ''                             # Path to SSL certificate
     ssl_key: ''                              # Path to SSL private key
   ```

4. **Password Setup:**

   The password is typically set through the web interface on first setup. Alternatively, you can generate a password hash manually:
   ```bash
   python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
   ```
   Then paste the generated hash into `password_hash` in your config.yaml.

5. Important Raspberry Pi Configuration Notes:
   - When using GPIO, ensure pins are correctly configured for your specific hardware setup
   - For I2C sensors, verify the correct I2C address in your configuration
   - Consider using a lower `collection_interval` for critical sensors
   - Adjust `batch_size` and `transmission_interval` based on your network conditions
   - Enable `persistence_enabled` to prevent data loss during power outages

### 5. Running the Application

1. Start the main application:
   ```bash
   python3 -m app.main
   ```

### 6. Running on Startup (Optional)

1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/growassistant.service
   ```

2. Add the following content (adjust paths as needed):
   ```ini
   [Unit]
   Description=GrowAssistant Bridge
   After=network.target

   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/GrowAssistant-Bridge
   Environment=PATH=/home/pi/GrowAssistant-Bridge/venv/bin
   ExecStart=/home/pi/GrowAssistant-Bridge/venv/bin/python -m app.main
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

3. Enable and start the service:
   ```bash
   sudo systemctl enable growassistant
   sudo systemctl start growassistant
   ```

## Creating Custom Integrations

#### Method 1: Built-in Integrations

1. Create a new directory in `app/integrations/` for your integration
2. Create an `__init__.py` file with your integration class
3. Implement the required methods from the `Integration` base class
4. Register your integration using the `@register_integration` decorator

Example:

```python
from app.integrations import Integration, register_integration

@register_integration
class MyIntegration(Integration):
    async def connect(self):
        # Implementation
        return True

    async def send_data(self, data):
        # Implementation
        return True

    async def receive_data(self):
        # Implementation
        yield {"value": 42}
```

#### Method 2: External Integrations (Recommended for Users)

The application now supports external integrations that can be added without modifying the core codebase:

1. Create a Python file in the `external_integrations/` directory
2. Implement your integration class inheriting from Integration base class
3. Register your integration using the `@register_integration` decorator
4. Add configuration for your integration in `config.yaml`
5. Restart the application

Example:

```python
from app.integrations import Integration, register_integration

@register_integration
class MyCustomIntegration(Integration):
    # Implementation of required methods...
```

For more details, see the [Custom Integrations Guide](docs/custom_integrations.md) and check the examples in the `external_integrations/` directory.

## Logs

The application maintains several log files:

- `logs/app.log`: Main application log file
- `logs/api_values/`: Individual log files for each value sent to the API

The API value logs contain detailed information about each value sent to the external API, including:
- The data sent
- API URL used
- Success/failure status
- Error messages (if any)

These logs are useful for debugging API communication issues and tracking the history of values sent to the API.

## Project Structure

```
├── app/                      # Application package
│   ├── __init__.py           # Package initialization
│   ├── main.py               # Main application entry point
│   ├── config.py             # Configuration handling
│   ├── mqtt_transport.py     # MQTT transport to the GrowAssistant app
│   ├── entity_id.py          # Shared <domain>.<name> entity-id derivation
│   ├── auth.py               # Pairing, JWT rotation, credential manager
│   ├── constants.py          # Application constants
│   ├── queue_manager.py      # Data queue management
│   ├── registry.py           # Device type registry
│   ├── watchdog.py           # Process watchdog for reliability
│   ├── schemas/              # Configuration schemas
│   │   └── config_schemas.py # Pydantic config validation
│   ├── utils/                # Utility modules
│   │   ├── http_utils.py     # HTTP utilities
│   │   ├── sensitive_data.py # Sensitive data handling
│   │   ├── singleton.py      # Singleton pattern
│   │   └── validation.py     # Validation utilities
│   └── integrations/         # Integration modules
│       ├── __init__.py       # Integration base class
│       ├── manifest.py       # Integration manifest handling
│       ├── gpio/             # GPIO integration
│       ├── mqtt/             # MQTT integration
│       ├── http/             # HTTP integration
│       └── serial/           # Serial integration
├── external_integrations/    # External integrations (user plugins)
│   ├── README.md             # Instructions for creating external integrations
│   ├── sample_integration.py # Template for creating new integrations
│   ├── sample_config.yaml    # Example configuration for sample integration
│   ├── dht_sensor.py         # DHT temperature/humidity sensor integration
│   ├── dht_config.yaml       # DHT sensor configuration example
│   └── climate_control.py    # Climate control integration example
├── web/                      # Web interface
│   ├── __init__.py           # Package initialization
│   ├── app.py                # Web application
│   ├── templates/            # HTML templates
│   │   ├── base.html         # Base template
│   │   ├── index.html        # Dashboard
│   │   ├── login.html        # Login page
│   │   ├── setup.html        # Initial setup
│   │   ├── onboarding.html   # Onboarding flow
│   │   ├── config.html       # Configuration page
│   │   ├── error.html        # Error page
│   │   └── partials/         # Partial templates
│   └── static/               # Static assets
│       ├── css/              # Stylesheets (Tailwind)
│       └── js/               # JavaScript files
├── logs/                     # Log files
│   ├── app.log               # Main application log
│   └── api_values/           # API value logs
├── tests/                    # Test directory
├── docs/                     # Documentation
│   ├── custom_integrations.md # Guide for developing custom integrations
│   └── developer_guide.md    # Developer documentation
├── config.example.yaml       # Example configuration (copy to config.yaml)
├── config.yaml               # Your configuration file (not in git)
├── requirements.txt          # Python dependencies
├── package.json              # Node.js dependencies (for Tailwind CSS)
├── setup.sh                  # Interactive setup script (recommended)
├── setup-dev.sh              # Developer setup script
└── README.md                 # This file
```

## Troubleshooting

### Common Raspberry Pi Issues

1. GPIO Access Denied:
   - Ensure you've added your user to the gpio group
   - Try logging out and back in
   - Check permissions with `ls -l /dev/gpio*`

2. I2C Issues:
   - Verify I2C is enabled in raspi-config
   - Check I2C devices with `i2cdetect -y 1`
   - Ensure proper wiring and pull-up resistors

3. Memory Issues:
   - Monitor memory usage with `free -h`
   - Consider increasing swap space if needed
   - Close unnecessary applications

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

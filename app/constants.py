"""
Application Constants.

This module defines named constants to replace magic numbers throughout
the application, improving readability and maintainability.
"""

# =============================================================================
# Timing Constants (in seconds unless otherwise specified)
# =============================================================================

# Default intervals
DEFAULT_COLLECTION_INTERVAL = 60  # seconds between data collections
DEFAULT_POLL_INTERVAL = 30  # seconds between API polls for commands
DEFAULT_TRANSMISSION_INTERVAL = 10  # seconds between data transmissions
DEFAULT_CONNECTION_TIMEOUT = 300  # seconds to wait for connection
DEFAULT_FLUSH_INTERVAL = 300  # seconds between queue flushes (5 minutes)

# Polling intervals
AUTH_POLL_INTERVAL = 5  # seconds between auth status checks
SPACE_CREATION_POLL_INTERVAL = 30  # seconds between space creation checks

# Timeouts
DEFAULT_HTTP_TIMEOUT = 30.0  # seconds for HTTP requests
TASK_SHUTDOWN_TIMEOUT = 5.0  # seconds to wait for task cancellation

# Retry settings
DEFAULT_RETRY_MAX_ATTEMPTS = 5
DEFAULT_RETRY_MIN_BACKOFF = 1  # seconds
DEFAULT_RETRY_MAX_BACKOFF = 60  # seconds


# =============================================================================
# Queue Constants
# =============================================================================

DEFAULT_MAX_QUEUE_SIZE = 10000
DEFAULT_BATCH_SIZE = 100
MAX_CONSECUTIVE_FAILURES = 5  # failures before re-checking auth


# =============================================================================
# API Constants
# =============================================================================

# HTTP Status Codes
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_INTERNAL_ERROR = 500

# Timestamp conversion
MILLISECONDS_PER_SECOND = 1000


# =============================================================================
# Logging Constants
# =============================================================================

DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


# =============================================================================
# Sensor Value Ranges
# =============================================================================

class SensorRanges:
    """Acceptable ranges for different sensor types."""

    TEMPERATURE_MIN = -10
    TEMPERATURE_MAX = 50

    HUMIDITY_MIN = 0
    HUMIDITY_MAX = 100

    PH_MIN = 0
    PH_MAX = 14

    TANK_ML_MIN = 0
    TANK_ML_MAX = None  # No maximum


# =============================================================================
# Problem Priority Levels
# =============================================================================

class ProblemPriority:
    """Priority levels for problems (0-100)."""

    LOW = 25
    MEDIUM = 50
    HIGH = 70
    CRITICAL = 90


# =============================================================================
# Web Interface Constants
# =============================================================================

DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 5010
CONCURRENT_EXECUTOR_WORKERS = 10


# =============================================================================
# File Paths (relative to project root)
# =============================================================================

DEFAULT_CONFIG_FILE = "config.yaml"
DEFAULT_LOG_FILE = "logs/app.log"
DEFAULT_DATA_DIR = "data"
DEFAULT_CREDENTIALS_FILE = "data/credentials.json"
DEFAULT_QUEUE_DB_FILE = "data/queue.db"
DEFAULT_EXTERNAL_INTEGRATIONS_DIR = "external_integrations"

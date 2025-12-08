"""
Type Definitions Module.

This module provides TypedDict definitions for type-safe dictionary structures
used throughout the application. These definitions improve code quality by
enabling static type checking and IDE autocompletion.
"""

from typing import Any, Dict, List, Optional, TypedDict, Union

# =============================================================================
# API Data Structures
# =============================================================================


class DataLogDict(TypedDict):
    """Data log entry sent to the API."""

    logDate: str  # ISO format datetime
    logType: str  # LogType enum value
    value: str


class DataLogWithPumpDict(DataLogDict, total=False):
    """Data log entry with optional pump number."""

    pumpNum: int


class ProblemDict(TypedDict):
    """Problem report sent to the API."""

    id: str  # UUID
    priority: int  # 0-100
    description: str
    type: str  # ProblemType enum value
    status: str  # ProblemStatus enum value
    userCanResolve: bool
    resolved: bool


class ActionResponseDict(TypedDict):
    """Action response sent to the API."""

    id: str
    received: bool
    resolved: bool


class ActionDict(TypedDict):
    """Action received from the API."""

    id: str
    type: str  # ActionType enum value
    value: str
    pump_number: Optional[int]
    received: bool
    resolved: bool


# =============================================================================
# API Request/Response Payloads
# =============================================================================


class SendDataPayload(TypedDict):
    """Payload for sending data to the API."""

    dataLogs: List[DataLogDict]
    problems: List[ProblemDict]
    actions: List[ActionResponseDict]


class APIResponseSettings(TypedDict, total=False):
    """Settings received in API response."""

    rdh_mode: bool
    status: str
    light: Dict[str, Any]
    climate: Dict[str, Any]
    tank: Dict[str, Any]


class ParsedAPIResponse(TypedDict):
    """Parsed API response structure."""

    rdh_mode: bool
    status: str
    light: Dict[str, Any]
    climate: Dict[str, Any]
    tank: Dict[str, Any]
    actions: List[ActionDict]


# =============================================================================
# Configuration Structures
# =============================================================================


class APIConfigDict(TypedDict, total=False):
    """API configuration section."""

    url: str
    batch_size: int
    poll_interval: int
    transmission_interval: int
    connection_timeout: int
    log_values: bool
    retry_max_attempts: int
    retry_min_backoff: int
    retry_max_backoff: int
    verify_ssl: bool  # For HTTPS validation


class GeneralConfigDict(TypedDict, total=False):
    """General configuration section."""

    collection_interval: int
    log_level: str
    log_file: str
    data_dir: str
    external_integrations_dir: str


class QueueConfigDict(TypedDict, total=False):
    """Queue configuration section."""

    persistence_enabled: bool
    max_queue_size: int
    flush_interval: int
    persistence_file: str


class WebConfigDict(TypedDict, total=False):
    """Web interface configuration section."""

    enabled: bool
    host: str
    port: int
    auth_enabled: bool
    username: str
    password_hash: str
    ssl_enabled: bool
    ssl_cert: str
    ssl_key: str


class GPIOPinConfigDict(TypedDict, total=False):
    """GPIO pin configuration."""

    name: str
    pin: int
    direction: str  # "IN" or "OUT"
    pull: str  # "UP", "DOWN", or "NONE"
    initial: str  # "HIGH" or "LOW"


class MQTTTopicConfigDict(TypedDict, total=False):
    """MQTT topic configuration."""

    name: str
    type: str  # Device type
    qos: int


class HTTPEndpointConfigDict(TypedDict, total=False):
    """HTTP endpoint configuration."""

    name: str
    url: str
    method: str  # "GET", "POST", etc.
    headers: Dict[str, str]
    interval: int


class DeviceConfigDict(TypedDict, total=False):
    """Generic device configuration."""

    name: str
    type: str  # Device type (temperature, humidity, pump, etc.)
    enabled: bool


class IntegrationConfigDict(TypedDict, total=False):
    """Integration configuration."""

    enabled: bool
    # GPIO specific
    pins: Dict[str, GPIOPinConfigDict]
    # MQTT specific
    broker: str
    port: int
    topics: Dict[str, MQTTTopicConfigDict]
    # HTTP specific
    endpoints: Dict[str, HTTPEndpointConfigDict]
    # Generic devices
    devices: Dict[str, DeviceConfigDict]
    update_interval: int


class AppConfigDict(TypedDict, total=False):
    """Complete application configuration."""

    api: APIConfigDict
    general: GeneralConfigDict
    integrations: Dict[str, IntegrationConfigDict]
    queue: QueueConfigDict
    web: WebConfigDict


# =============================================================================
# Data Point Structures (Legacy and Internal)
# =============================================================================


class LegacyDataPointDict(TypedDict, total=False):
    """Legacy data point format for backward compatibility."""

    type: str
    value: Union[str, float, int]
    timestamp: int  # Milliseconds since epoch
    integration: str
    source: str
    endpoint_name: str
    sensor: str
    pumpNum: int
    pump_num: int


class QueueItemDict(TypedDict, total=False):
    """Item stored in the data queue."""

    timestamp: float  # Unix timestamp
    data: Dict[str, Any]


# =============================================================================
# Integration Data Structures
# =============================================================================


class IntegrationDeviceDataDict(TypedDict, total=False):
    """Device data returned from integrations."""

    name: str
    type: str
    value: Union[str, float, int, None]
    status: str
    last_updated: str


class IntegrationStatusDict(TypedDict, total=False):
    """Integration status information."""

    connected: bool
    last_data_received: Optional[str]
    error: Optional[str]
    devices: Dict[str, IntegrationDeviceDataDict]


# =============================================================================
# Authentication Structures
# =============================================================================


class CredentialsDict(TypedDict, total=False):
    """Stored credentials structure."""

    client_id: str
    custom_id: str
    registration_time: str
    token: str
    connected: bool
    ready: bool


class ConnectionStatusDict(TypedDict):
    """Connection status information."""

    connected: bool
    status: str  # "not_connected", "connected", "ready"


# =============================================================================
# Web API Response Structures
# =============================================================================


class WebAPIErrorResponse(TypedDict):
    """Standard error response for web API."""

    error: str


class WebAPISuccessResponse(TypedDict, total=False):
    """Standard success response for web API."""

    success: bool
    message: str


class DeviceListResponse(TypedDict):
    """Response containing list of devices."""

    devices: Dict[str, Dict[str, IntegrationDeviceDataDict]]


class QueueInfoResponse(TypedDict):
    """Queue status information response."""

    size: int
    is_empty: bool
    persistence_enabled: bool

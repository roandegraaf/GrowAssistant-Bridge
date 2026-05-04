"""API Types for communication with the GrowAssistant API.

These types MUST match the API (ValueType, ProblemType, ProblemStatus enums).
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Union


class ActionType(str, Enum):
    """Action types mapping to API's ValueType enum."""

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LIGHT = "LIGHT"
    FAN = "FAN"
    TANK_ML = "TANK_ML"
    PH_VALUE = "PH_VALUE"
    PH_ML = "PH_ML"
    SUPPLEMENT_ML = "SUPPLEMENT_ML"


class ProblemStatus(str, Enum):
    """Problem status categories (MUST match API's ProblemStatus enum)."""

    CONNECTION = "CONNECTION"
    EMPTY = "EMPTY"
    RANGE = "RANGE"
    OTHER = "OTHER"


class ProblemType(str, Enum):
    """Problem types (MUST match API's ProblemType enum)."""

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LIGHT = "LIGHT"
    FAN = "FAN"
    TANK = "TANK"
    SUPPLEMENT = "SUPPLEMENT"
    PH = "PH"
    CLIENT = "CLIENT"
    PLANT = "PLANT"
    SPACE = "SPACE"


class LogType(str, Enum):
    """Data log types mapping to API's ValueType enum."""

    # Sensor types
    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LIGHT = "LIGHT"
    FAN = "FAN"
    TANK_ML = "TANK_ML"
    TANK_LEVEL = "TANK_LEVEL"
    PH_VALUE = "PH_VALUE"
    PH_LEVEL = "PH_LEVEL"
    PH_ML = "PH_ML"
    SUPPLEMENT_ML = "SUPPLEMENT_ML"
    SUPPLEMENT_LEVEL = "SUPPLEMENT_LEVEL"
    PLANT_WATER = "PLANT_WATER"
    SOIL_MOISTURE = "SOIL_MOISTURE"

    # Binary actuator states
    HEATER_STATE = "HEATER_STATE"
    FAN_STATE = "FAN_STATE"
    HUMIDIFIER_STATE = "HUMIDIFIER_STATE"
    DEHUMIDIFIER_STATE = "DEHUMIDIFIER_STATE"
    LIGHT_STATE = "LIGHT_STATE"

    # Variable actuator states
    FAN_SPEED = "FAN_SPEED"
    LIGHT_LEVEL = "LIGHT_LEVEL"


def create_data_log(
    log_type: Union[LogType, str],
    value: Union[str, float, int],
    log_date: Optional[datetime] = None,
    device_id: Optional[str] = None,
) -> dict:
    """Create a data log entry for the API."""
    log_type_str = log_type if isinstance(log_type, str) else log_type.value
    log = {
        "logDate": (log_date or datetime.utcnow()).isoformat(),
        "logType": log_type_str.upper(),
        "value": str(value),
    }
    if device_id:
        log["deviceId"] = device_id
    return log


def create_problem(
    problem_type: Union[ProblemType, str],
    status: Union[ProblemStatus, str],
    description: str,
    priority: int = 0,
    user_can_resolve: bool = True,
    resolved: bool = False,
    problem_id: Optional[str] = None,
) -> dict:
    """Create a problem report for the API."""
    return {
        "id": problem_id or str(uuid.uuid4()),
        "priority": priority,
        "description": description,
        "type": problem_type if isinstance(problem_type, str) else problem_type.value,
        "status": status if isinstance(status, str) else status.value,
        "userCanResolve": user_can_resolve,
        "resolved": resolved,
    }


def create_action_response(action_id: str, received: bool = True, resolved: bool = False) -> dict:
    """Create an action response for the API."""
    return {"id": action_id, "received": received, "resolved": resolved}


def parse_api_response(response_data: dict) -> dict:
    """Parse API response into structured data."""
    return {
        "rdh_mode": response_data.get("rdhMode", False),
        "status": response_data.get("status", ""),
        "light": response_data.get("light", {}),
        "climate": response_data.get("climate", {}),
        "tank": response_data.get("tank", {}),
        "actions": [
            {
                "id": action.get("id", ""),
                "type": action.get("type", ""),
                "value": action.get("value", ""),
                "received": action.get("received", False),
                "resolved": action.get("resolved", False),
            }
            for action in response_data.get("actions", [])
        ],
    }

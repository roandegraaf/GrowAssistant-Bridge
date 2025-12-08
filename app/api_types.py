"""
API Types Module.

This module defines the data types and constants used for API communication.
These types MUST match the API (ValueType, ProblemType, ProblemStatus enums).
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Union


# Action Types (maps to API ValueType enum)
class ActionType(str, Enum):
    """Types of actions that can be performed or requested.
    Maps to API's ValueType enum."""

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LIGHT = "LIGHT"
    FAN = "FAN"
    TANK_ML = "TANK_ML"
    PH_VALUE = "PH_VALUE"
    PH_ML = "PH_ML"
    SUPPLEMENT_ML = "SUPPLEMENT_ML"


# Problem Status (matches API ProblemStatus enum)
class ProblemStatus(str, Enum):
    """Status categories for problems.
    MUST match API's ProblemStatus enum exactly."""

    CONNECTION = "CONNECTION"
    EMPTY = "EMPTY"
    RANGE = "RANGE"
    OTHER = "OTHER"


# Problem Types (matches API ProblemType enum)
class ProblemType(str, Enum):
    """Types of problems that can occur.
    MUST match API's ProblemType enum exactly."""

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


# Data Log Types (maps to API ValueType enum)
class LogType(str, Enum):
    """Types of data logs.
    Maps to API's ValueType enum - use these for sending sensor data."""

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LIGHT = "LIGHT"
    FAN = "FAN"
    TANK_ML = "TANK_ML"
    PH_VALUE = "PH_VALUE"
    PH_ML = "PH_ML"
    SUPPLEMENT_ML = "SUPPLEMENT_ML"
    PLANT_WATER = "PLANT_WATER"


# Helper functions for creating API data structures
def create_data_log(
    log_type: Union[LogType, str],
    value: Union[str, float, int],
    log_date: Optional[datetime] = None,
) -> Dict:
    """Create a data log entry.

    Args:
        log_type: Type of log data
        value: Value to log
        log_date: Timestamp for the log (defaults to now)

    Returns:
        Dict: Data log entry in the format expected by the API
    """
    if log_date is None:
        log_date = datetime.utcnow()

    return {
        "logDate": log_date.isoformat(),
        "logType": log_type if isinstance(log_type, str) else log_type.value,
        "value": str(value),
    }


def create_problem(
    problem_type: Union[ProblemType, str],
    status: Union[ProblemStatus, str],
    description: str,
    priority: int = 0,
    user_can_resolve: bool = True,
    resolved: bool = False,
    problem_id: Optional[str] = None,
) -> Dict:
    """Create a problem report.

    Args:
        problem_type: Type of problem
        status: Status category
        description: Description of the problem
        priority: Problem priority (0-100, higher is more urgent)
        user_can_resolve: Whether the user can resolve this problem
        resolved: Whether the problem is already resolved
        problem_id: Optional UUID for the problem (generated if not provided)

    Returns:
        Dict: Problem entry in the format expected by the API
    """
    if problem_id is None:
        problem_id = str(uuid.uuid4())

    return {
        "id": problem_id,
        "priority": priority,
        "description": description,
        "type": problem_type if isinstance(problem_type, str) else problem_type.value,
        "status": status if isinstance(status, str) else status.value,
        "userCanResolve": user_can_resolve,
        "resolved": resolved,
    }


def create_action_response(action_id: str, received: bool = True, resolved: bool = False) -> Dict:
    """Create an action response.

    Args:
        action_id: ID of the action being responded to
        received: Whether the action was received
        resolved: Whether the action was resolved/completed

    Returns:
        Dict: Action response in the format expected by the API
    """
    return {"id": action_id, "received": received, "resolved": resolved}


# Helper function to parse API response
def parse_api_response(response_data: Dict) -> Dict:
    """Parse the API response.

    Args:
        response_data: Raw API response data

    Returns:
        Dict: Structured data extracted from the response
    """
    result = {
        "rdh_mode": response_data.get("rdhMode", False),
        "status": response_data.get("status", ""),
        "light": response_data.get("light", {}),
        "climate": response_data.get("climate", {}),
        "tank": response_data.get("tank", {}),
        "actions": [],
    }

    # Process actions
    for action_data in response_data.get("actions", []):
        result["actions"].append(
            {
                "id": action_data.get("id", ""),
                "type": action_data.get("type", ""),
                "value": action_data.get("value", ""),
                "pump_number": action_data.get("pumpNumber"),
                "received": action_data.get("received", False),
                "resolved": action_data.get("resolved", False),
            }
        )

    return result

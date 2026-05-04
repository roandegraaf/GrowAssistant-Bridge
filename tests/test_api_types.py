"""
Tests for the API types module.

This module tests the data types, enums, and helper functions
used for API communication.
"""

import uuid
from datetime import datetime

from app.api_types import (
    ActionType,
    LogType,
    ProblemStatus,
    ProblemType,
    create_action_response,
    create_data_log,
    create_problem,
    parse_api_response,
)


class TestActionType:
    """Tests for ActionType enum."""

    def test_action_type_values(self):
        """Test ActionType enum values."""
        assert ActionType.TEMPERATURE.value == "TEMPERATURE"
        assert ActionType.HUMIDITY.value == "HUMIDITY"
        assert ActionType.LIGHT.value == "LIGHT"
        assert ActionType.FAN.value == "FAN"
        assert ActionType.TANK_ML.value == "TANK_ML"
        assert ActionType.PH_VALUE.value == "PH_VALUE"

    def test_action_type_string_enum(self):
        """Test ActionType is a string enum."""
        assert isinstance(ActionType.LIGHT, str)
        assert ActionType.LIGHT == "LIGHT"


class TestLogType:
    """Tests for LogType enum."""

    def test_log_type_values(self):
        """Test LogType enum values."""
        assert LogType.TEMPERATURE.value == "TEMPERATURE"
        assert LogType.HUMIDITY.value == "HUMIDITY"
        assert LogType.LIGHT.value == "LIGHT"
        assert LogType.TANK_ML.value == "TANK_ML"
        assert LogType.PLANT_WATER.value == "PLANT_WATER"


class TestProblemStatus:
    """Tests for ProblemStatus enum."""

    def test_problem_status_values(self):
        """Test ProblemStatus enum values."""
        assert ProblemStatus.CONNECTION.value == "CONNECTION"
        assert ProblemStatus.EMPTY.value == "EMPTY"
        assert ProblemStatus.RANGE.value == "RANGE"
        assert ProblemStatus.OTHER.value == "OTHER"


class TestProblemType:
    """Tests for ProblemType enum."""

    def test_problem_type_values(self):
        """Test ProblemType enum values."""
        assert ProblemType.TEMPERATURE.value == "TEMPERATURE"
        assert ProblemType.HUMIDITY.value == "HUMIDITY"
        assert ProblemType.TANK.value == "TANK"
        assert ProblemType.CLIENT.value == "CLIENT"
        assert ProblemType.PLANT.value == "PLANT"


class TestCreateDataLog:
    """Tests for create_data_log function."""

    def test_create_data_log_basic(self):
        """Test creating a basic data log."""
        log = create_data_log(LogType.TEMPERATURE, 25.5)

        assert log["logType"] == "TEMPERATURE"
        assert log["value"] == "25.5"
        assert "logDate" in log

    def test_create_data_log_with_enum(self):
        """Test creating data log with enum type."""
        log = create_data_log(LogType.HUMIDITY, 60)

        assert log["logType"] == "HUMIDITY"

    def test_create_data_log_with_string(self):
        """Test creating data log with string type."""
        log = create_data_log("CUSTOM_TYPE", 100)

        assert log["logType"] == "CUSTOM_TYPE"

    def test_create_data_log_with_custom_date(self):
        """Test creating data log with custom date."""
        custom_date = datetime(2024, 6, 15, 10, 30, 0)
        log = create_data_log(LogType.TEMPERATURE, 25.0, log_date=custom_date)

        assert "2024-06-15" in log["logDate"]
        assert "10:30:00" in log["logDate"]

    def test_create_data_log_value_as_string(self):
        """Test that value is converted to string."""
        log = create_data_log(LogType.PH_VALUE, 6.5)

        assert log["value"] == "6.5"
        assert isinstance(log["value"], str)

    def test_create_data_log_integer_value(self):
        """Test creating data log with integer value."""
        log = create_data_log(LogType.TANK_ML, 1000)

        assert log["value"] == "1000"


class TestCreateProblem:
    """Tests for create_problem function."""

    def test_create_problem_basic(self):
        """Test creating a basic problem."""
        problem = create_problem(
            ProblemType.TEMPERATURE, ProblemStatus.RANGE, "Temperature out of acceptable range"
        )

        assert problem["type"] == "TEMPERATURE"
        assert problem["status"] == "RANGE"
        assert problem["description"] == "Temperature out of acceptable range"
        assert problem["priority"] == 0
        assert problem["userCanResolve"] is True
        assert problem["resolved"] is False
        assert "id" in problem

    def test_create_problem_with_priority(self):
        """Test creating problem with custom priority."""
        problem = create_problem(
            ProblemType.TANK, ProblemStatus.EMPTY, "Tank is empty", priority=80
        )

        assert problem["priority"] == 80

    def test_create_problem_with_custom_id(self):
        """Test creating problem with custom ID."""
        custom_id = "custom-problem-id"
        problem = create_problem(
            ProblemType.CLIENT, ProblemStatus.CONNECTION, "Connection lost", problem_id=custom_id
        )

        assert problem["id"] == custom_id

    def test_create_problem_user_cannot_resolve(self):
        """Test creating problem user cannot resolve."""
        problem = create_problem(
            ProblemType.SPACE, ProblemStatus.OTHER, "System error", user_can_resolve=False
        )

        assert problem["userCanResolve"] is False

    def test_create_problem_already_resolved(self):
        """Test creating already resolved problem."""
        problem = create_problem(
            ProblemType.HUMIDITY, ProblemStatus.RANGE, "Humidity normalized", resolved=True
        )

        assert problem["resolved"] is True

    def test_create_problem_id_is_uuid(self):
        """Test that generated problem ID is valid UUID."""
        problem = create_problem(ProblemType.LIGHT, ProblemStatus.OTHER, "Light issue")

        # Should not raise
        uuid.UUID(problem["id"])

    def test_create_problem_with_string_enums(self):
        """Test creating problem with string types."""
        problem = create_problem("CUSTOM_TYPE", "CUSTOM_STATUS", "Custom problem")

        assert problem["type"] == "CUSTOM_TYPE"
        assert problem["status"] == "CUSTOM_STATUS"


class TestCreateActionResponse:
    """Tests for create_action_response function."""

    def test_create_action_response_basic(self):
        """Test creating basic action response."""
        response = create_action_response("action-123")

        assert response["id"] == "action-123"
        assert response["received"] is True
        assert response["resolved"] is False

    def test_create_action_response_resolved(self):
        """Test creating resolved action response."""
        response = create_action_response("action-456", received=True, resolved=True)

        assert response["resolved"] is True

    def test_create_action_response_not_received(self):
        """Test creating action response not received."""
        response = create_action_response("action-789", received=False)

        assert response["received"] is False


class TestParseApiResponse:
    """Tests for parse_api_response function."""

    def test_parse_empty_response(self):
        """Test parsing empty response."""
        result = parse_api_response({})

        assert result["rdh_mode"] is False
        assert result["status"] == ""
        assert result["light"] == {}
        assert result["climate"] == {}
        assert result["tank"] == {}
        assert result["actions"] == []

    def test_parse_full_response(self, sample_api_response):
        """Test parsing full API response."""
        result = parse_api_response(sample_api_response)

        assert result["rdh_mode"] is False
        assert result["status"] == "active"
        assert result["light"]["day"]["on"] == "06:00"
        assert result["climate"]["temperature"] == 25
        assert result["tank"]["ph"] == 6.5
        assert len(result["actions"]) == 1

    def test_parse_response_with_actions(self):
        """Test parsing response with actions."""
        response = {
            "actions": [
                {
                    "id": "action-1",
                    "type": "LIGHT",
                    "value": "on",
                    "received": False,
                    "resolved": False,
                },
                {
                    "id": "action-2",
                    "type": "FAN",
                    "value": "50",
                    "received": True,
                    "resolved": False,
                },
            ]
        }

        result = parse_api_response(response)

        assert len(result["actions"]) == 2
        assert result["actions"][0]["id"] == "action-1"
        assert result["actions"][0]["type"] == "LIGHT"
        # Regression guard: legacy pump_number must not be in parsed output.
        assert "pump_number" not in result["actions"][0]
        assert "pump_number" not in result["actions"][1]

    def test_parse_response_rdh_mode(self):
        """Test parsing response with RDH mode enabled."""
        response = {"rdhMode": True}

        result = parse_api_response(response)

        assert result["rdh_mode"] is True

    def test_parse_response_with_missing_action_fields(self):
        """Test parsing actions with missing fields."""
        response = {"actions": [{"id": "action-1"}]}  # Missing most fields

        result = parse_api_response(response)

        assert len(result["actions"]) == 1
        assert result["actions"][0]["id"] == "action-1"
        assert result["actions"][0]["type"] == ""
        assert result["actions"][0]["value"] == ""

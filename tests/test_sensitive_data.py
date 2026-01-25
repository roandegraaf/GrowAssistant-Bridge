"""
Tests for Sensitive Data Masking Utilities.

This module tests the sensitive data masking and unmasking functionality
used to prevent accidental exposure of sensitive configuration values.
"""

import pytest

from app.utils.sensitive_data import (
    DEFAULT_MASK,
    DEFAULT_SENSITIVE_PATHS,
    SENSITIVE_KEYS,
    get_safe_config_for_logging,
    mask_sensitive_data,
    unmask_sensitive_data,
)


class TestMaskSensitiveData:
    """Tests for the mask_sensitive_data function."""

    def test_masks_default_sensitive_paths(self):
        """Test that default sensitive paths are masked."""
        data = {
            "api": {"auth_token": "secret123"},
            "web": {"password_hash": "hash456", "secret_key": "key789"},
        }
        result = mask_sensitive_data(data)

        assert result["api"]["auth_token"] == DEFAULT_MASK
        assert result["web"]["password_hash"] == DEFAULT_MASK
        assert result["web"]["secret_key"] == DEFAULT_MASK

    def test_masks_sensitive_keys(self):
        """Test that sensitive keys are masked regardless of path."""
        data = {
            "custom": {
                "password": "mypassword",
                "api_key": "apikey123",
                "token": "token456",
                "private_key": "privatekey789",
            }
        }
        result = mask_sensitive_data(data)

        assert result["custom"]["password"] == DEFAULT_MASK
        assert result["custom"]["api_key"] == DEFAULT_MASK
        assert result["custom"]["token"] == DEFAULT_MASK
        assert result["custom"]["private_key"] == DEFAULT_MASK

    def test_case_insensitive_key_matching(self):
        """Test that key matching is case-insensitive."""
        data = {
            "PASSWORD": "pass1",
            "Password": "pass2",
            "API_KEY": "key1",
            "Api_Key": "key2",
        }
        result = mask_sensitive_data(data)

        assert result["PASSWORD"] == DEFAULT_MASK
        assert result["Password"] == DEFAULT_MASK
        assert result["API_KEY"] == DEFAULT_MASK
        assert result["Api_Key"] == DEFAULT_MASK

    def test_preserves_non_sensitive_values(self):
        """Test that non-sensitive values are preserved."""
        data = {
            "api": {"url": "http://example.com", "timeout": 30},
            "web": {"host": "0.0.0.0", "port": 5000},
        }
        result = mask_sensitive_data(data)

        assert result["api"]["url"] == "http://example.com"
        assert result["api"]["timeout"] == 30
        assert result["web"]["host"] == "0.0.0.0"
        assert result["web"]["port"] == 5000

    def test_handles_nested_dicts(self):
        """Test that nested dictionaries are processed correctly."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "password": "deeppassword",
                        "name": "test",
                    }
                }
            }
        }
        result = mask_sensitive_data(data)

        assert result["level1"]["level2"]["level3"]["password"] == DEFAULT_MASK
        assert result["level1"]["level2"]["level3"]["name"] == "test"

    def test_handles_lists_of_dicts(self):
        """Test that lists containing dictionaries are processed."""
        data = {
            "users": [
                {"name": "user1", "password": "pass1"},
                {"name": "user2", "password": "pass2"},
            ]
        }
        result = mask_sensitive_data(data)

        assert result["users"][0]["name"] == "user1"
        assert result["users"][0]["password"] == DEFAULT_MASK
        assert result["users"][1]["name"] == "user2"
        assert result["users"][1]["password"] == DEFAULT_MASK

    def test_custom_mask_string(self):
        """Test using a custom mask string."""
        data = {"api": {"auth_token": "secret123"}}
        custom_mask = "[REDACTED]"
        result = mask_sensitive_data(data, mask=custom_mask)

        assert result["api"]["auth_token"] == custom_mask

    def test_custom_sensitive_paths(self):
        """Test using custom sensitive paths."""
        data = {
            "custom": {"my_secret": "secret123"},
            "api": {"auth_token": "shouldnotmask"},
        }
        custom_paths = {"custom.my_secret"}
        result = mask_sensitive_data(data, sensitive_paths=custom_paths, sensitive_keys=set())

        assert result["custom"]["my_secret"] == DEFAULT_MASK
        assert result["api"]["auth_token"] == "shouldnotmask"

    def test_custom_sensitive_keys(self):
        """Test using custom sensitive keys."""
        data = {
            "config": {"my_secret_field": "secret123"},
            "api": {"password": "shouldnotmask"},
        }
        custom_keys = {"my_secret_field"}
        result = mask_sensitive_data(data, sensitive_paths=set(), sensitive_keys=custom_keys)

        assert result["config"]["my_secret_field"] == DEFAULT_MASK
        assert result["api"]["password"] == "shouldnotmask"

    def test_does_not_mask_empty_values(self):
        """Test that empty string values are not masked."""
        data = {"api": {"auth_token": "", "password": ""}}
        result = mask_sensitive_data(data)

        assert result["api"]["auth_token"] == ""
        assert result["api"]["password"] == ""

    def test_does_not_mask_none_values(self):
        """Test that None values are not masked."""
        data = {"api": {"auth_token": None, "password": None}}
        result = mask_sensitive_data(data)

        assert result["api"]["auth_token"] is None
        assert result["api"]["password"] is None

    def test_returns_deep_copy(self):
        """Test that the result is a deep copy of the original."""
        original = {"api": {"url": "http://example.com", "auth_token": "secret"}}
        result = mask_sensitive_data(original)

        # Modify result to verify it doesn't affect original
        result["api"]["url"] = "http://modified.com"

        assert original["api"]["url"] == "http://example.com"

    def test_handles_empty_dict(self):
        """Test handling of empty dictionary."""
        result = mask_sensitive_data({})
        assert result == {}

    def test_handles_mixed_types_in_list(self):
        """Test that non-dict items in lists are preserved."""
        data = {
            "items": [
                {"password": "secret"},
                "string_item",
                123,
                None,
                {"name": "test"},
            ]
        }
        result = mask_sensitive_data(data)

        assert result["items"][0]["password"] == DEFAULT_MASK
        assert result["items"][1] == "string_item"
        assert result["items"][2] == 123
        assert result["items"][3] is None
        assert result["items"][4]["name"] == "test"


class TestUnmaskSensitiveData:
    """Tests for the unmask_sensitive_data function."""

    def test_restores_masked_values(self):
        """Test that masked values are restored from original data."""
        original = {"api": {"auth_token": "secret123", "url": "http://example.com"}}
        masked = {"api": {"auth_token": DEFAULT_MASK, "url": "http://newurl.com"}}

        result = unmask_sensitive_data(masked, original)

        assert result["api"]["auth_token"] == "secret123"
        assert result["api"]["url"] == "http://newurl.com"

    def test_preserves_non_masked_values(self):
        """Test that non-masked values are preserved."""
        original = {"api": {"auth_token": "old_secret", "url": "http://old.com"}}
        masked = {"api": {"auth_token": "new_secret", "url": "http://new.com"}}

        result = unmask_sensitive_data(masked, original)

        assert result["api"]["auth_token"] == "new_secret"
        assert result["api"]["url"] == "http://new.com"

    def test_handles_nested_dicts(self):
        """Test unmasking in nested dictionaries."""
        original = {"level1": {"level2": {"password": "deeppassword", "name": "oldname"}}}
        masked = {"level1": {"level2": {"password": DEFAULT_MASK, "name": "newname"}}}

        result = unmask_sensitive_data(masked, original)

        assert result["level1"]["level2"]["password"] == "deeppassword"
        assert result["level1"]["level2"]["name"] == "newname"

    def test_handles_lists_of_dicts(self):
        """Test unmasking in lists of dictionaries."""
        original = {
            "users": [
                {"name": "user1", "password": "pass1"},
                {"name": "user2", "password": "pass2"},
            ]
        }
        masked = {
            "users": [
                {"name": "newuser1", "password": DEFAULT_MASK},
                {"name": "newuser2", "password": DEFAULT_MASK},
            ]
        }

        result = unmask_sensitive_data(masked, original)

        assert result["users"][0]["name"] == "newuser1"
        assert result["users"][0]["password"] == "pass1"
        assert result["users"][1]["name"] == "newuser2"
        assert result["users"][1]["password"] == "pass2"

    def test_handles_missing_keys_in_original(self):
        """Test that new keys not in original are preserved."""
        original = {"api": {"auth_token": "secret123"}}
        masked = {"api": {"auth_token": DEFAULT_MASK, "new_key": "new_value"}}

        result = unmask_sensitive_data(masked, original)

        assert result["api"]["auth_token"] == "secret123"
        assert result["api"]["new_key"] == "new_value"

    def test_custom_mask_string(self):
        """Test using a custom mask string for unmasking."""
        custom_mask = "[REDACTED]"
        original = {"api": {"auth_token": "secret123"}}
        masked = {"api": {"auth_token": custom_mask}}

        result = unmask_sensitive_data(masked, original, mask=custom_mask)

        assert result["api"]["auth_token"] == "secret123"

    def test_returns_deep_copy(self):
        """Test that the result is a deep copy."""
        original = {"api": {"auth_token": "secret123"}}
        masked = {"api": {"auth_token": DEFAULT_MASK}}

        result = unmask_sensitive_data(masked, original)
        result["api"]["auth_token"] = "modified"

        assert original["api"]["auth_token"] == "secret123"

    def test_handles_empty_dicts(self):
        """Test handling of empty dictionaries."""
        result = unmask_sensitive_data({}, {})
        assert result == {}

    def test_handles_mismatched_list_lengths(self):
        """Test handling when masked list is longer than original."""
        original = {"items": [{"password": "pass1"}]}
        masked = {
            "items": [
                {"password": DEFAULT_MASK},
                {"password": DEFAULT_MASK},  # Extra item
            ]
        }

        result = unmask_sensitive_data(masked, original)

        assert result["items"][0]["password"] == "pass1"
        assert result["items"][1]["password"] == DEFAULT_MASK  # No original to restore


class TestGetSafeConfigForLogging:
    """Tests for the get_safe_config_for_logging function."""

    def test_masks_default_fields(self):
        """Test that default sensitive fields are masked."""
        config = {
            "api": {"url": "http://example.com", "auth_token": "secret123"},
            "web": {"host": "0.0.0.0", "password_hash": "hash456"},
        }

        result = get_safe_config_for_logging(config)

        assert result["api"]["url"] == "http://example.com"
        assert result["api"]["auth_token"] == DEFAULT_MASK
        assert result["web"]["host"] == "0.0.0.0"
        assert result["web"]["password_hash"] == DEFAULT_MASK

    def test_adds_additional_masks(self):
        """Test that additional mask paths are applied."""
        config = {
            "api": {"url": "http://example.com", "auth_token": "secret"},
            "custom": {"my_secret": "sensitive_data"},
        }

        result = get_safe_config_for_logging(config, additional_masks=["custom.my_secret"])

        assert result["api"]["auth_token"] == DEFAULT_MASK
        assert result["custom"]["my_secret"] == DEFAULT_MASK

    def test_returns_copy(self):
        """Test that the result is a copy and doesn't modify original."""
        original = {"api": {"auth_token": "secret123", "url": "http://example.com"}}

        result = get_safe_config_for_logging(original)
        result["api"]["url"] = "modified"

        assert original["api"]["url"] == "http://example.com"

    def test_handles_empty_additional_masks(self):
        """Test handling when additional_masks is empty."""
        config = {"api": {"auth_token": "secret"}}

        result = get_safe_config_for_logging(config, additional_masks=[])

        assert result["api"]["auth_token"] == DEFAULT_MASK

    def test_handles_none_additional_masks(self):
        """Test handling when additional_masks is None."""
        config = {"api": {"auth_token": "secret"}}

        result = get_safe_config_for_logging(config, additional_masks=None)

        assert result["api"]["auth_token"] == DEFAULT_MASK


class TestConstants:
    """Tests for module constants."""

    def test_default_mask_value(self):
        """Test the default mask string."""
        assert DEFAULT_MASK == "**********"

    def test_default_sensitive_paths_content(self):
        """Test that default sensitive paths contain expected values."""
        expected_paths = {
            "api.auth_token",
            "web.password_hash",
            "web.secret_key",
            "credentials.token",
            "credentials.password",
        }
        assert DEFAULT_SENSITIVE_PATHS == expected_paths

    def test_sensitive_keys_content(self):
        """Test that sensitive keys contain expected values."""
        expected_keys = {
            "password",
            "password_hash",
            "secret",
            "secret_key",
            "auth_token",
            "api_key",
            "token",
            "private_key",
        }
        assert SENSITIVE_KEYS == expected_keys

"""
Tests for ConfigStore module.

This module tests local configuration persistence, version tracking,
and outbound queue functionality using SQLite.
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config_store import ConfigStore


@pytest.fixture
def temp_db_file(tmp_path):
    """Create a temporary database file path."""
    db_file = tmp_path / "test_config.db"
    yield str(db_file)
    # Cleanup
    if db_file.exists():
        db_file.unlink()


@pytest.fixture
def config_store_with_temp_db(temp_db_file, mock_config):
    """Create a ConfigStore instance with a temporary database."""
    mock_config.get = MagicMock(return_value=temp_db_file)

    with patch("app.config_store.config", mock_config):
        store = ConfigStore()
        yield store
        store.stop()


class TestConfigStoreInit:
    """Tests for ConfigStore initialization."""

    def test_init_sets_default_values(self, mock_config):
        """Test that initialization sets correct default values."""
        mock_config.get = MagicMock(return_value="data/config.db")

        with patch("app.config_store.config", mock_config):
            store = ConfigStore()

            assert store._db_conn is None
            assert store._initialized is False
            assert store._db_file == "data/config.db"

    def test_init_with_custom_db_file(self, mock_config):
        """Test initialization with custom database file."""
        mock_config.get = MagicMock(return_value="/custom/path/config.db")

        with patch("app.config_store.config", mock_config):
            store = ConfigStore()

            assert store._db_file == "/custom/path/config.db"


class TestConfigStoreStartStop:
    """Tests for ConfigStore start and stop methods."""

    def test_start_creates_database(self, config_store_with_temp_db, temp_db_file):
        """Test that start creates the database."""
        store = config_store_with_temp_db
        store.start()

        assert store._initialized is True
        assert store._db_conn is not None
        assert os.path.exists(temp_db_file)

    def test_start_creates_tables(self, config_store_with_temp_db):
        """Test that start creates required tables."""
        store = config_store_with_temp_db
        store.start()

        cursor = store._db_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_config'")
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='outbound_queue'"
        )
        assert cursor.fetchone() is not None

    def test_start_creates_directory_if_not_exists(self, tmp_path, mock_config):
        """Test that start creates database directory if it doesn't exist."""
        db_file = tmp_path / "subdir" / "config.db"
        mock_config.get = MagicMock(return_value=str(db_file))

        with patch("app.config_store.config", mock_config):
            store = ConfigStore()
            store.start()

            assert db_file.parent.exists()
            store.stop()

    def test_start_prevents_double_start(self, config_store_with_temp_db):
        """Test that calling start twice doesn't re-initialize."""
        store = config_store_with_temp_db
        store.start()
        first_conn = store._db_conn

        store.start()

        assert store._db_conn is first_conn

    def test_stop_closes_connection(self, config_store_with_temp_db):
        """Test that stop closes database connection."""
        store = config_store_with_temp_db
        store.start()

        store.stop()

        assert store._db_conn is None
        assert store._initialized is False

    def test_stop_when_not_started(self, config_store_with_temp_db):
        """Test stop when store is not started."""
        store = config_store_with_temp_db

        # Should not raise
        store.stop()

        assert store._db_conn is None


class TestConfigStoreConfigOperations:
    """Tests for config storage and retrieval."""

    def test_save_and_get_config(self, config_store_with_temp_db):
        """Test saving and retrieving configuration."""
        store = config_store_with_temp_db
        store.start()

        config_data = {"light": {"on": "06:00", "off": "22:00"}}
        store.save_config("test_key", config_data, 1)

        result = store.get_config("test_key")
        assert result == config_data

    def test_save_config_with_version(self, config_store_with_temp_db):
        """Test that config version is saved correctly."""
        store = config_store_with_temp_db
        store.start()

        config_data = {"test": "value"}
        store.save_config("test_key", config_data, 42)

        cursor = store._db_conn.cursor()
        cursor.execute("SELECT version FROM local_config WHERE key = ?", ("test_key",))
        row = cursor.fetchone()
        assert row[0] == 42

    def test_save_config_updates_existing(self, config_store_with_temp_db):
        """Test that saving config with same key updates existing entry."""
        store = config_store_with_temp_db
        store.start()

        store.save_config("key1", {"value": 1}, 1)
        store.save_config("key1", {"value": 2}, 2)

        result = store.get_config("key1")
        assert result == {"value": 2}

    def test_get_config_nonexistent_key(self, config_store_with_temp_db):
        """Test getting config with nonexistent key."""
        store = config_store_with_temp_db
        store.start()

        result = store.get_config("nonexistent")

        assert result is None

    def test_get_config_invalid_json(self, config_store_with_temp_db):
        """Test getting config with invalid JSON."""
        store = config_store_with_temp_db
        store.start()

        # Insert invalid JSON directly
        store._db_conn.execute(
            "INSERT INTO local_config (key, value, version, updated_at) VALUES (?, ?, ?, ?)",
            ("bad_key", "invalid{json", 1, time.time()),
        )
        store._db_conn.commit()

        result = store.get_config("bad_key")
        assert result is None

    def test_get_config_when_not_started(self, config_store_with_temp_db):
        """Test getting config when store is not started."""
        store = config_store_with_temp_db

        result = store.get_config("any_key")

        assert result is None

    def test_save_config_when_not_started(self, config_store_with_temp_db):
        """Test saving config when store is not started."""
        store = config_store_with_temp_db

        # Should not raise, but should log warning
        store.save_config("key", {"value": 1}, 1)

        # Verify nothing was saved
        store.start()
        result = store.get_config("key")
        assert result is None


class TestConfigStoreFullConfig:
    """Tests for full config operations."""

    def test_save_and_get_full_config(self, config_store_with_temp_db):
        """Test saving and retrieving full configuration."""
        store = config_store_with_temp_db
        store.start()

        config_data = {
            "light": {"on": "06:00", "off": "22:00"},
            "climate": {"temperature": 25, "humidity": 60},
        }
        store.save_full_config(config_data, 5)

        result, version = store.get_full_config()
        assert result == config_data
        assert version == 5

    def test_get_full_config_when_empty(self, config_store_with_temp_db):
        """Test getting full config when nothing is stored."""
        store = config_store_with_temp_db
        store.start()

        result, version = store.get_full_config()

        assert result is None
        assert version == 0

    def test_get_full_config_when_not_started(self, config_store_with_temp_db):
        """Test getting full config when store is not started."""
        store = config_store_with_temp_db

        result, version = store.get_full_config()

        assert result is None
        assert version == 0

    def test_get_full_config_invalid_json(self, config_store_with_temp_db):
        """Test getting full config with invalid JSON."""
        store = config_store_with_temp_db
        store.start()

        # Insert invalid JSON
        store._db_conn.execute(
            "INSERT INTO local_config (key, value, version, updated_at) VALUES (?, ?, ?, ?)",
            ("full", "not valid json{", 1, time.time()),
        )
        store._db_conn.commit()

        result, version = store.get_full_config()
        assert result is None
        assert version == 0


class TestConfigStoreVersionTracking:
    """Tests for config version tracking."""

    def test_get_config_version_when_empty(self, config_store_with_temp_db):
        """Test getting config version when nothing is stored."""
        store = config_store_with_temp_db
        store.start()

        version = store.get_config_version()

        assert version == 0

    def test_get_config_version_returns_stored_version(self, config_store_with_temp_db):
        """Test that get_config_version returns the stored version."""
        store = config_store_with_temp_db
        store.start()

        store.save_full_config({"test": "data"}, 123)

        version = store.get_config_version()
        assert version == 123

    def test_get_config_version_when_not_started(self, config_store_with_temp_db):
        """Test getting config version when store is not started."""
        store = config_store_with_temp_db

        version = store.get_config_version()

        assert version == 0


class TestConfigStoreOutboundQueue:
    """Tests for outbound request queue."""

    def test_queue_outbound_request(self, config_store_with_temp_db):
        """Test queuing an outbound request."""
        store = config_store_with_temp_db
        store.start()

        payload = {"data": "test", "value": 42}
        store.queue_outbound("/api/endpoint", payload)

        # Verify it was saved
        cursor = store._db_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM outbound_queue")
        count = cursor.fetchone()[0]
        assert count == 1

    def test_queue_outbound_when_not_started(self, config_store_with_temp_db):
        """Test queuing outbound when store is not started."""
        store = config_store_with_temp_db

        # Should not raise
        store.queue_outbound("/api/endpoint", {"data": "test"})

        # Verify nothing was saved
        store.start()
        pending = store.get_pending_outbound()
        assert len(pending) == 0

    def test_get_pending_outbound(self, config_store_with_temp_db):
        """Test getting pending outbound requests."""
        store = config_store_with_temp_db
        store.start()

        payload1 = {"data": "test1"}
        payload2 = {"data": "test2"}
        store.queue_outbound("/api/endpoint1", payload1)
        store.queue_outbound("/api/endpoint2", payload2)

        pending = store.get_pending_outbound()

        assert len(pending) == 2
        assert pending[0][1] == "/api/endpoint1"
        assert pending[0][2] == payload1
        assert pending[1][1] == "/api/endpoint2"
        assert pending[1][2] == payload2

    def test_get_pending_outbound_when_empty(self, config_store_with_temp_db):
        """Test getting pending outbound when queue is empty."""
        store = config_store_with_temp_db
        store.start()

        pending = store.get_pending_outbound()

        assert pending == []

    def test_get_pending_outbound_when_not_started(self, config_store_with_temp_db):
        """Test getting pending outbound when store is not started."""
        store = config_store_with_temp_db

        pending = store.get_pending_outbound()

        assert pending == []

    def test_get_pending_outbound_with_invalid_json(self, config_store_with_temp_db):
        """Test getting pending outbound with invalid JSON payload."""
        store = config_store_with_temp_db
        store.start()

        # Insert invalid JSON directly
        store._db_conn.execute(
            "INSERT INTO outbound_queue (endpoint, payload, created_at) VALUES (?, ?, ?)",
            ("/api/test", "invalid{json", time.time()),
        )
        store._db_conn.commit()

        pending = store.get_pending_outbound()

        # Should skip invalid entries
        assert len(pending) == 0

    def test_remove_outbound(self, config_store_with_temp_db):
        """Test removing an outbound request."""
        store = config_store_with_temp_db
        store.start()

        store.queue_outbound("/api/endpoint", {"data": "test"})
        pending = store.get_pending_outbound()
        row_id = pending[0][0]

        store.remove_outbound(row_id)

        pending_after = store.get_pending_outbound()
        assert len(pending_after) == 0

    def test_remove_outbound_when_not_started(self, config_store_with_temp_db):
        """Test removing outbound when store is not started."""
        store = config_store_with_temp_db

        # Should not raise
        store.remove_outbound(999)

    def test_outbound_queue_ordering(self, config_store_with_temp_db):
        """Test that outbound requests are returned in order."""
        store = config_store_with_temp_db
        store.start()

        # Add multiple requests with small delays
        for i in range(3):
            store.queue_outbound(f"/api/endpoint{i}", {"order": i})
            time.sleep(0.01)

        pending = store.get_pending_outbound()

        assert len(pending) == 3
        assert pending[0][2]["order"] == 0
        assert pending[1][2]["order"] == 1
        assert pending[2][2]["order"] == 2


class TestConfigStoreSingleton:
    """Tests for ConfigStore singleton behavior."""

    def test_singleton_returns_same_instance(self, mock_config):
        """Test that ConfigStore returns the same instance."""
        mock_config.get = MagicMock(return_value="test.db")

        with patch("app.config_store.config", mock_config):
            store1 = ConfigStore()
            store2 = ConfigStore()

            assert store1 is store2

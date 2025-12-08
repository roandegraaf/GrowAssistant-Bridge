"""
Tests for the QueueManager module.

This module tests queue operations, persistence, batching,
and data point management.
"""

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestQueueManager:
    """Tests for the QueueManager class."""

    @pytest.fixture
    def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            # Reset the singleton
            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            yield qm

    @pytest.mark.asyncio
    async def test_put_adds_item_to_queue(self, queue_manager, sample_data_point):
        """Test that put adds an item to the queue."""
        success = await queue_manager.put(sample_data_point)

        assert success is True
        assert queue_manager.size() == 1
        assert queue_manager.is_empty() is False

    @pytest.mark.asyncio
    async def test_put_adds_timestamp_if_missing(self, queue_manager):
        """Test that put adds a timestamp if not present."""
        data = {"type": "temperature", "value": 25.0}

        await queue_manager.put(data)
        item = await queue_manager.get(timeout=1.0)

        assert "timestamp" in item
        assert isinstance(item["timestamp"], float)

    @pytest.mark.asyncio
    async def test_get_retrieves_item(self, queue_manager, sample_data_point):
        """Test that get retrieves an item from the queue."""
        await queue_manager.put(sample_data_point)

        item = await queue_manager.get(timeout=1.0)

        assert item == sample_data_point
        assert queue_manager.is_empty() is True

    @pytest.mark.asyncio
    async def test_get_returns_none_on_timeout(self, queue_manager):
        """Test that get returns None when timeout occurs."""
        item = await queue_manager.get(timeout=0.1)

        assert item is None

    @pytest.mark.asyncio
    async def test_get_batch_retrieves_multiple_items(self, queue_manager, sample_data_points):
        """Test that get_batch retrieves multiple items."""
        for dp in sample_data_points:
            await queue_manager.put(dp)

        items = await queue_manager.get_batch(max_items=5, timeout=1.0)

        assert len(items) == len(sample_data_points)
        assert queue_manager.is_empty() is True

    @pytest.mark.asyncio
    async def test_get_batch_respects_max_items(self, queue_manager, sample_data_points):
        """Test that get_batch respects max_items limit."""
        for dp in sample_data_points:
            await queue_manager.put(dp)

        items = await queue_manager.get_batch(max_items=2, timeout=1.0)

        assert len(items) == 2
        assert queue_manager.size() == 1

    @pytest.mark.asyncio
    async def test_get_data_points_alias(self, queue_manager, sample_data_point):
        """Test that get_data_points is an alias for get_batch."""
        await queue_manager.put(sample_data_point)

        items = await queue_manager.get_data_points(max_items=10, timeout=1.0)

        assert len(items) == 1
        assert items[0] == sample_data_point

    @pytest.mark.asyncio
    async def test_requeue_data_points(self, queue_manager, sample_data_points):
        """Test that requeue_data_points adds items back to the queue."""
        await queue_manager.requeue_data_points(sample_data_points)

        assert queue_manager.size() == len(sample_data_points)

    @pytest.mark.asyncio
    async def test_mark_processed(self, queue_manager, sample_data_points):
        """Test that mark_processed calls task_done for each item."""
        for dp in sample_data_points:
            await queue_manager.put(dp)

        items = await queue_manager.get_batch(max_items=10, timeout=1.0)

        # This should not raise an error
        await queue_manager.mark_processed(items)

    def test_size_returns_queue_size(self, queue_manager):
        """Test that size returns the current queue size."""
        assert queue_manager.size() == 0

    def test_is_empty_returns_true_for_empty_queue(self, queue_manager):
        """Test that is_empty returns True for empty queue."""
        assert queue_manager.is_empty() is True


class TestQueueManagerPersistence:
    """Tests for QueueManager persistence functionality."""

    @pytest.fixture
    def queue_manager_with_persistence(self, tmp_path, sample_config):
        """Create a QueueManager with persistence enabled."""
        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(tmp_path / "queue.db")

        mock_config = MagicMock()
        mock_config.config = sample_config

        def get_side_effect(key, default=None):
            parts = key.split(".")
            value = sample_config
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return default
            return value

        mock_config.get.side_effect = get_side_effect

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            yield qm

    @pytest.mark.asyncio
    async def test_start_initializes_database(self, queue_manager_with_persistence, tmp_path):
        """Test that start initializes the database."""
        await queue_manager_with_persistence.start()

        db_path = tmp_path / "queue.db"
        assert db_path.exists()

        await queue_manager_with_persistence.stop()

    @pytest.mark.asyncio
    async def test_flush_to_db_persists_items(self, queue_manager_with_persistence, tmp_path, sample_data_points):
        """Test that flush_to_db persists items to database."""
        await queue_manager_with_persistence.start()

        for dp in sample_data_points:
            await queue_manager_with_persistence.put(dp)

        await queue_manager_with_persistence._flush_to_db()

        # Check database has items
        db_path = tmp_path / "queue.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queue")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == len(sample_data_points)
        assert queue_manager_with_persistence.is_empty() is True

        await queue_manager_with_persistence.stop()

    @pytest.mark.asyncio
    async def test_load_from_db_restores_items(self, tmp_path, sample_config, sample_data_point):
        """Test that load_from_db restores items from database."""
        db_path = tmp_path / "queue.db"

        # Pre-populate database
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """)
        cursor.execute(
            "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
            (sample_data_point["timestamp"], json.dumps(sample_data_point))
        )
        conn.commit()
        conn.close()

        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(db_path)

        mock_config = MagicMock()

        def get_side_effect(key, default=None):
            parts = key.split(".")
            value = sample_config
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return default
            return value

        mock_config.get.side_effect = get_side_effect

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            await qm.start()

            assert qm.size() == 1
            item = await qm.get(timeout=1.0)
            assert item == sample_data_point

            await qm.stop()

    @pytest.mark.asyncio
    async def test_stop_flushes_remaining_items(self, queue_manager_with_persistence, tmp_path, sample_data_point):
        """Test that stop flushes remaining items to database."""
        await queue_manager_with_persistence.start()
        await queue_manager_with_persistence.put(sample_data_point)
        await queue_manager_with_persistence.stop()

        # Check database has the item
        db_path = tmp_path / "queue.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queue")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1


class TestQueueManagerConcurrency:
    """Tests for QueueManager concurrent operations."""

    @pytest.fixture
    def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            yield qm

    @pytest.mark.asyncio
    async def test_concurrent_puts(self, queue_manager):
        """Test that concurrent puts work correctly."""
        async def put_item(i):
            await queue_manager.put({"value": i, "timestamp": i * 1000})

        await asyncio.gather(*[put_item(i) for i in range(100)])

        assert queue_manager.size() == 100

    @pytest.mark.asyncio
    async def test_concurrent_gets(self, queue_manager):
        """Test that concurrent gets work correctly."""
        for i in range(100):
            await queue_manager.put({"value": i, "timestamp": i * 1000})

        async def get_item():
            return await queue_manager.get(timeout=1.0)

        results = await asyncio.gather(*[get_item() for _ in range(100)])

        assert len([r for r in results if r is not None]) == 100
        assert queue_manager.is_empty() is True

    @pytest.mark.asyncio
    async def test_producer_consumer_pattern(self, queue_manager):
        """Test producer-consumer pattern works correctly."""
        produced = []
        consumed = []

        async def producer():
            for i in range(50):
                data = {"value": i}
                await queue_manager.put(data)
                produced.append(data)
                await asyncio.sleep(0.001)

        async def consumer():
            for _ in range(50):
                item = await queue_manager.get(timeout=2.0)
                if item:
                    consumed.append(item)

        await asyncio.gather(producer(), consumer())

        assert len(produced) == 50
        assert len(consumed) == 50

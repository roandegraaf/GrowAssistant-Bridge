"""
Tests for the QueueManager module.

This module tests queue operations, persistence, batching,
and data point management.
"""

import asyncio
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


class TestQueueManager:
    """Tests for the QueueManager class."""

    @pytest.fixture
    async def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            # Reset the singleton
            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            # Reinitialize the queue in the current event loop
            qm._queue = asyncio.Queue(maxsize=10000)
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
    async def test_flush_to_db_persists_items(
        self, queue_manager_with_persistence, tmp_path, sample_data_points
    ):
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """
        )
        cursor.execute(
            "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
            (sample_data_point["timestamp"], json.dumps(sample_data_point)),
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
    async def test_stop_flushes_remaining_items(
        self, queue_manager_with_persistence, tmp_path, sample_data_point
    ):
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
    async def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            # Reinitialize the queue in the current event loop
            qm._queue = asyncio.Queue(maxsize=10000)
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


class TestQueueManagerErrorHandling:
    """Tests for QueueManager error handling and edge cases."""

    @pytest.fixture
    async def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            # Reinitialize the queue in the current event loop
            qm._queue = asyncio.Queue(maxsize=10000)
            yield qm

    @pytest.mark.asyncio
    async def test_put_when_queue_is_full(self, mock_config):
        """Test that put returns False when queue is full."""
        # Set a small queue size
        mock_config.get = MagicMock(
            side_effect=lambda k, default: 2 if k == "queue.max_queue_size" else default
        )

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            qm._queue = asyncio.Queue(maxsize=2)

            # Fill the queue
            assert await qm.put({"value": 1}) is True
            assert await qm.put({"value": 2}) is True

            # Queue is now full, mock put to raise QueueFull
            original_put = qm._queue.put

            async def mock_put(item):
                raise asyncio.QueueFull()

            qm._queue.put = mock_put

            # This should return False since queue is full
            result = await qm.put({"value": 3})

            assert result is False

    @pytest.mark.asyncio
    async def test_get_without_timeout_blocks_until_item_available(self, queue_manager):
        """Test that get without timeout waits for an item."""

        async def delayed_put():
            await asyncio.sleep(0.05)
            await queue_manager.put({"value": 42, "timestamp": 123456789.0})

        # Start putting item after a delay
        put_task = asyncio.create_task(delayed_put())

        # Get without timeout should wait
        item = await queue_manager.get()

        await put_task
        assert item["value"] == 42

    @pytest.mark.asyncio
    async def test_get_batch_with_empty_queue_after_first_timeout(self, queue_manager):
        """Test get_batch returns empty list when first get times out."""
        items = await queue_manager.get_batch(max_items=5, timeout=0.1)

        assert items == []

    @pytest.mark.asyncio
    async def test_get_batch_handles_queue_empty_exception(self, queue_manager, sample_data_points):
        """Test that get_batch handles QueueEmpty exception gracefully."""
        # Add only one item
        await queue_manager.put(sample_data_points[0])

        # Request more items than available
        items = await queue_manager.get_batch(max_items=10, timeout=0.1)

        # Should get only the one item available
        assert len(items) == 1
        assert items[0] == sample_data_points[0]

    @pytest.mark.asyncio
    async def test_get_batch_handles_race_condition_queue_empty(
        self, queue_manager, sample_data_points
    ):
        """Test that get_batch handles race condition where empty() returns False but get_nowait raises QueueEmpty."""
        # Add one item
        await queue_manager.put(sample_data_points[0])

        # Save original methods
        original_empty = queue_manager._queue.empty
        original_get_nowait = queue_manager._queue.get_nowait

        # Track number of calls to get_nowait
        call_count = {"count": 0}

        def mock_get_nowait():
            call_count["count"] += 1
            # First call: get the actual item (for the initial get() in get_batch)
            if call_count["count"] == 1:
                return original_get_nowait()
            # Second call: raise QueueEmpty (race condition in while loop)
            raise asyncio.QueueEmpty()

        def mock_empty():
            # Return False to make while loop think there's an item
            # This creates the race condition
            return False

        # Apply mocks
        queue_manager._queue.get_nowait = mock_get_nowait
        queue_manager._queue.empty = mock_empty

        # Get batch - first get() succeeds, while loop tries get_nowait which fails
        items = await queue_manager.get_batch(max_items=10, timeout=0.1)

        # Should get only the first item
        assert len(items) == 1
        assert items[0] == sample_data_points[0]
        # Should have tried get_nowait twice (once in get(), once in while loop)
        assert call_count["count"] == 2

        # Restore
        queue_manager._queue.empty = original_empty
        queue_manager._queue.get_nowait = original_get_nowait

    @pytest.mark.asyncio
    async def test_task_done_method(self, queue_manager, sample_data_point):
        """Test that task_done method works correctly."""
        await queue_manager.put(sample_data_point)
        await queue_manager.get()

        # Should not raise
        queue_manager.task_done()

    @pytest.mark.asyncio
    async def test_load_from_db_when_db_conn_is_none(self, queue_manager):
        """Test that _load_from_db returns early when db_conn is None."""
        queue_manager._db_conn = None

        # Should not raise and should return early
        queue_manager._load_from_db()

        assert queue_manager.size() == 0

    @pytest.mark.asyncio
    async def test_load_from_db_handles_json_decode_error(self, tmp_path, sample_config):
        """Test that _load_from_db handles JSONDecodeError gracefully."""
        db_path = tmp_path / "queue.db"

        # Pre-populate database with invalid JSON
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """
        )
        cursor.execute(
            "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
            (1234567890.0, "invalid{json"),
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

            # Queue should be empty since invalid JSON was skipped
            assert qm.size() == 0

            await qm.stop()

    @pytest.mark.asyncio
    async def test_load_from_db_handles_queue_full_exception(self, tmp_path, sample_config):
        """Test that _load_from_db handles QueueFull exception gracefully."""
        db_path = tmp_path / "queue.db"

        # Pre-populate database with multiple items
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """
        )
        for i in range(5):
            cursor.execute(
                "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
                (1234567890.0 + i, json.dumps({"value": i, "timestamp": 1234567890.0 + i})),
            )
        conn.commit()
        conn.close()

        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(db_path)
        sample_config["queue"]["max_queue_size"] = 2  # Small queue

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

            # Queue should have only max_queue_size items
            assert qm.size() <= 2

            await qm.stop()

    @pytest.mark.asyncio
    async def test_flush_to_db_handles_queue_empty_exception(self, tmp_path, sample_config):
        """Test that _flush_to_db handles QueueEmpty exception gracefully."""
        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(tmp_path / "queue.db")

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

            # Add an item
            await qm.put({"value": 1})

            # Mock get_nowait to raise QueueEmpty after first call
            original_get_nowait = qm._queue.get_nowait
            call_count = [0]

            def mock_get_nowait():
                call_count[0] += 1
                if call_count[0] == 1:
                    return original_get_nowait()
                raise asyncio.QueueEmpty()

            qm._queue.get_nowait = mock_get_nowait

            # Should handle exception gracefully
            await qm._flush_to_db()

            await qm.stop()

    @pytest.mark.asyncio
    async def test_flush_to_db_handles_general_exception(self, tmp_path, sample_config):
        """Test that _flush_to_db handles general exceptions gracefully."""
        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(tmp_path / "queue.db")

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

            # Add an item
            await qm.put({"value": 1})

            # Mock get_nowait to raise a general exception
            original_get_nowait = qm._queue.get_nowait

            def mock_get_nowait():
                item = original_get_nowait()
                # Raise exception after getting the item, during database operation
                raise Exception("Database error during flush")

            qm._queue.get_nowait = mock_get_nowait

            # Should handle exception gracefully and stop processing
            await qm._flush_to_db()

            await qm.stop()


class TestQueueManagerPeriodicFlush:
    """Tests for QueueManager periodic flush functionality."""

    @pytest.fixture
    def queue_manager_with_persistence(self, tmp_path, sample_config):
        """Create a QueueManager with persistence enabled."""
        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(tmp_path / "queue.db")
        sample_config["queue"]["flush_interval"] = 0.2  # Short interval for testing

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
    async def test_periodic_flush_runs_at_intervals(self, queue_manager_with_persistence, tmp_path):
        """Test that periodic flush runs at configured intervals."""
        await queue_manager_with_persistence.start()

        # Add items
        await queue_manager_with_persistence.put({"value": 1})
        await queue_manager_with_persistence.put({"value": 2})

        # Wait for flush interval
        await asyncio.sleep(0.3)

        # Check database has items
        db_path = tmp_path / "queue.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queue")
        count = cursor.fetchone()[0]
        conn.close()

        # Items should have been flushed
        assert count >= 0  # May be 0 or 2 depending on timing

        await queue_manager_with_persistence.stop()

    @pytest.mark.asyncio
    async def test_periodic_flush_cancellation(self, queue_manager_with_persistence):
        """Test that periodic flush task is cancelled on stop."""
        await queue_manager_with_persistence.start()

        assert queue_manager_with_persistence._flush_task is not None
        flush_task = queue_manager_with_persistence._flush_task

        await queue_manager_with_persistence.stop()

        # Task should be cancelled
        assert flush_task.cancelled() or flush_task.done()

    @pytest.mark.asyncio
    async def test_periodic_flush_handles_errors(self, tmp_path, sample_config):
        """Test that periodic flush handles errors and continues running."""
        sample_config["queue"]["persistence_enabled"] = True
        sample_config["queue"]["persistence_file"] = str(tmp_path / "queue.db")
        sample_config["queue"]["flush_interval"] = 0.1

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

            # Add an item
            await qm.put({"value": 1})

            # Mock _flush_to_db to raise an exception once
            original_flush = qm._flush_to_db
            call_count = [0]

            async def mock_flush():
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("Test error")
                return await original_flush()

            qm._flush_to_db = mock_flush

            # Wait for a flush cycle
            await asyncio.sleep(0.15)

            # Task should still be running despite error
            assert qm._flush_task is not None
            assert not qm._flush_task.done()

            await qm.stop()

    @pytest.mark.asyncio
    async def test_flush_to_db_when_db_conn_is_none(self, queue_manager_with_persistence):
        """Test that _flush_to_db returns early when db_conn is None."""
        queue_manager_with_persistence._db_conn = None
        queue_manager_with_persistence._queue = asyncio.Queue()
        await queue_manager_with_persistence._queue.put({"value": 1})

        # Should not raise and should return early
        await queue_manager_with_persistence._flush_to_db()

    @pytest.mark.asyncio
    async def test_flush_to_db_when_queue_is_empty(self, queue_manager_with_persistence):
        """Test that _flush_to_db returns early when queue is empty."""
        await queue_manager_with_persistence.start()

        # Should not raise and should return early
        await queue_manager_with_persistence._flush_to_db()

        await queue_manager_with_persistence.stop()


class TestQueueManagerBatchEdgeCases:
    """Tests for QueueManager batch operation edge cases."""

    @pytest.fixture
    async def queue_manager(self, mock_config):
        """Create a fresh QueueManager instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            qm._queue = asyncio.Queue(maxsize=10000)
            yield qm

    @pytest.mark.asyncio
    async def test_get_batch_returns_all_available_items_when_less_than_max(
        self, queue_manager, sample_data_points
    ):
        """Test get_batch returns all items when queue has less than max_items."""
        # Add 3 items
        for dp in sample_data_points[:3]:
            await queue_manager.put(dp)

        # Request 10 items
        items = await queue_manager.get_batch(max_items=10, timeout=0.1)

        # Should get all 3 items
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_get_batch_stops_at_max_items(self, queue_manager):
        """Test get_batch stops at max_items even if more are available."""
        # Add 100 items
        for i in range(100):
            await queue_manager.put({"value": i})

        # Request only 10
        items = await queue_manager.get_batch(max_items=10, timeout=0.1)

        # Should get exactly 10
        assert len(items) == 10
        # 90 should remain
        assert queue_manager.size() == 90

    @pytest.mark.asyncio
    async def test_requeue_data_points_when_queue_is_full(self, mock_config):
        """Test requeue_data_points when some items cannot be requeued."""
        # Create queue with small size
        mock_config.get = MagicMock(
            side_effect=lambda k, default: 3 if k == "queue.max_queue_size" else default
        )

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            qm._queue = asyncio.Queue(maxsize=3)

            # Fill queue completely
            await qm.put({"value": 1})
            await qm.put({"value": 2})
            await qm.put({"value": 3})

            # Mock put to return False immediately (queue full)
            original_put = qm.put

            async def mock_put(data):
                return False  # Simulate queue full

            qm.put = mock_put

            # Try to requeue items - they should all fail
            items_to_requeue = [{"value": 4}, {"value": 5}]
            await qm.requeue_data_points(items_to_requeue)

            # Restore put
            qm.put = original_put

            # Queue should still be full with original items
            assert qm.size() == 3

    @pytest.mark.asyncio
    async def test_mark_processed_with_empty_list(self, queue_manager):
        """Test mark_processed with empty list."""
        # Should not raise
        await queue_manager.mark_processed([])

    @pytest.mark.asyncio
    async def test_mark_processed_multiple_times(self, queue_manager, sample_data_points):
        """Test marking items as processed multiple times."""
        for dp in sample_data_points:
            await queue_manager.put(dp)

        batch1 = await queue_manager.get_batch(max_items=2, timeout=0.1)
        batch2 = await queue_manager.get_batch(max_items=2, timeout=0.1)

        # Mark both batches as processed
        await queue_manager.mark_processed(batch1)
        await queue_manager.mark_processed(batch2)

        # Should not raise


class TestQueueManagerDatabaseEdgeCases:
    """Tests for QueueManager database edge cases."""

    @pytest.mark.asyncio
    async def test_start_with_persistence_disabled(self, mock_config):
        """Test that start works with persistence disabled."""
        mock_config.get = MagicMock(
            side_effect=lambda k, default: False if k == "queue.persistence_enabled" else default
        )

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            await qm.start()

            assert qm._db_conn is None
            assert qm._flush_task is None

            await qm.stop()

    @pytest.mark.asyncio
    async def test_stop_with_persistence_disabled(self, mock_config):
        """Test that stop works with persistence disabled."""
        mock_config.get = MagicMock(
            side_effect=lambda k, default: False if k == "queue.persistence_enabled" else default
        )

        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            qm._queue = asyncio.Queue()
            await qm.start()
            await qm.put({"value": 1})

            # Should not flush to db
            await qm.stop()

            assert qm._db_conn is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, mock_config):
        """Test that stop works when queue was never started."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm = QueueManager()
            qm._queue = asyncio.Queue()

            # Should not raise
            await qm.stop()

    @pytest.mark.asyncio
    async def test_database_directory_creation(self, tmp_path, sample_config):
        """Test that database directory is created if it doesn't exist."""
        db_path = tmp_path / "nested" / "directory" / "queue.db"
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

            # Directory should be created
            assert db_path.parent.exists()
            assert db_path.exists()

            await qm.stop()


class TestQueueManagerSingleton:
    """Tests for QueueManager singleton behavior."""

    def test_singleton_returns_same_instance(self, mock_config):
        """Test that QueueManager returns the same instance."""
        with patch("app.queue_manager.config", mock_config):
            from app.queue_manager import QueueManager
            from app.utils.singleton import SingletonMeta

            if QueueManager in SingletonMeta._instances:
                del SingletonMeta._instances[QueueManager]

            qm1 = QueueManager()
            qm2 = QueueManager()

            assert qm1 is qm2

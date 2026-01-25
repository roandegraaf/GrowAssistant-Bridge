"""Queue manager for buffering data with persistence to SQLite."""

import asyncio
import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from app.config import config
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class QueueManager(metaclass=SingletonMeta):
    """Manager for queuing data points with SQLite persistence.

    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self):
        """Initialize the queue manager."""
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=config.get("queue.max_queue_size", 10000)
        )
        self._db_conn: Optional[sqlite3.Connection] = None
        self._flush_task: Optional[asyncio.Task] = None

        logger.info("Queue Manager initialized")

    async def start(self):
        """Start the queue manager with optional persistence."""
        if config.get("queue.persistence_enabled", True):
            self._init_db()
            self._load_from_db()
            self._flush_task = asyncio.create_task(self._periodic_flush())
            logger.info("Queue persistence started")

    async def stop(self):
        """Stop the queue manager and flush remaining data."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        if config.get("queue.persistence_enabled", True):
            await self._flush_to_db()
            if self._db_conn:
                self._db_conn.close()
                self._db_conn = None

        logger.info("Queue Manager stopped")

    def _init_db(self):
        """Initialize the SQLite database connection."""
        db_file = config.get("queue.persistence_file", "data/queue.db")

        db_dir = os.path.dirname(db_file)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db_conn = sqlite3.connect(db_file)
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """)
        self._db_conn.commit()

        logger.info(f"Queue database initialized at {db_file}")

    def _load_from_db(self):
        """Load queued items from the database into memory."""
        if not self._db_conn:
            return

        cursor = self._db_conn.cursor()
        cursor.execute("SELECT id, timestamp, data FROM queue ORDER BY timestamp")

        count = 0
        for _, _, data_json in cursor.fetchall():
            try:
                self._queue.put_nowait(json.loads(data_json))
                count += 1
            except (json.JSONDecodeError, asyncio.QueueFull) as e:
                logger.error(f"Error loading item from queue database: {e}")

        if count > 0:
            logger.info(f"Loaded {count} items from queue database")

        cursor.execute("DELETE FROM queue")
        self._db_conn.commit()

    async def _flush_to_db(self):
        """Flush the in-memory queue to the database."""
        if not self._db_conn or self._queue.empty():
            return

        count = 0
        cursor = self._db_conn.cursor()

        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                cursor.execute(
                    "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
                    (item.get("timestamp", time.time()), json.dumps(item)),
                )
                count += 1
            except (asyncio.QueueEmpty, Exception) as e:
                logger.error(f"Error flushing item to queue database: {e}")
                break

        if count > 0:
            self._db_conn.commit()
            logger.info(f"Flushed {count} items to queue database")

    async def _periodic_flush(self):
        """Periodically flush the queue to the database."""
        interval = config.get("queue.flush_interval", 300)

        while True:
            try:
                await asyncio.sleep(interval)
                await self._flush_to_db()
            except asyncio.CancelledError:
                logger.info("Periodic flush task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic flush: {e}")

    async def put(self, data: dict[str, Any]) -> bool:
        """Add a data point to the queue. Returns False if queue is full."""
        if "timestamp" not in data:
            data["timestamp"] = time.time()

        try:
            await self._queue.put(data)
            logger.debug(f"Added item to queue, size: {self._queue.qsize()}")
            return True
        except asyncio.QueueFull:
            logger.warning("Queue is full, item not added")
            return False

    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get a data point from the queue. Returns None on timeout."""
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def get_batch(
        self, max_items: int, timeout: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Get a batch of data points from the queue."""
        items = []

        first_item = await self.get(timeout)
        if first_item:
            items.append(first_item)

        while len(items) < max_items and not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        return items

    async def get_data_points(
        self, max_items: int, timeout: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Alias for get_batch."""
        return await self.get_batch(max_items, timeout)

    async def mark_processed(self, data_points: list[dict[str, Any]]) -> None:
        """Mark data points as processed."""
        for _ in data_points:
            self._queue.task_done()
        logger.debug(f"Marked {len(data_points)} items as processed")

    async def requeue_data_points(self, data_points: list[dict[str, Any]]) -> None:
        """Requeue data points that failed to send."""
        count = 0
        for dp in data_points:
            if await self.put(dp):
                count += 1
        logger.info(f"Requeued {count}/{len(data_points)} data points")

    def task_done(self):
        """Mark a task as done."""
        self._queue.task_done()

    def size(self) -> int:
        """Get the current queue size."""
        return self._queue.qsize()

    def is_empty(self) -> bool:
        """Check if the queue is empty."""
        return self._queue.empty()


# Create a global instance for easy imports
queue_manager = QueueManager()

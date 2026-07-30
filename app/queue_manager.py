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
        self._db_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        """
        )
        self._db_conn.commit()

        logger.info(f"Queue database initialized at {db_file}")

    def _load_from_db(self):
        """Load queued items from the database into memory.

        Only rows that are actually consumed here are deleted: a row loaded
        into the queue, or an unrecoverable (unparseable) row, is removed; but
        if the in-memory queue fills to capacity (QueueFull) partway through,
        the remaining rows are left persisted for a later start rather than
        being destroyed along with the loaded ones.
        """
        if not self._db_conn:
            return

        cursor = self._db_conn.cursor()
        cursor.execute("SELECT id, timestamp, data FROM queue ORDER BY timestamp")
        rows = cursor.fetchall()

        consumed_ids: list[int] = []
        loaded = 0
        for row_id, _, data_json in rows:
            try:
                item = json.loads(data_json)
            except json.JSONDecodeError as e:
                # Unrecoverable — drop it so it doesn't reload forever.
                logger.error(f"Discarding unparseable queue row {row_id}: {e}")
                consumed_ids.append(row_id)
                continue
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                logger.warning(
                    "Queue full while loading from database; "
                    f"{len(rows) - len(consumed_ids)} row(s) left persisted"
                )
                break
            consumed_ids.append(row_id)
            loaded += 1

        if loaded > 0:
            logger.info(f"Loaded {loaded} items from queue database")

        if consumed_ids:
            placeholders = ",".join("?" * len(consumed_ids))
            cursor.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", consumed_ids)
            self._db_conn.commit()

    async def _flush_to_db(self):
        """Flush the in-memory queue to the database.

        Drains the queue into a batch and writes it in a single transaction.
        Draining and (on failure) restoring happen without awaiting, so they
        are atomic with respect to the event loop. If the write fails, the
        drained items are put back on the queue rather than lost or abandoned
        mid-drain.
        """
        if not self._db_conn or self._queue.empty():
            return

        items: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not items:
            return

        rows = [(item.get("timestamp", time.time()), json.dumps(item)) for item in items]
        try:
            cursor = self._db_conn.cursor()
            cursor.executemany("INSERT INTO queue (timestamp, data) VALUES (?, ?)", rows)
            self._db_conn.commit()
            logger.info(f"Flushed {len(items)} items to queue database")
        except Exception as e:
            logger.error(
                f"Error flushing queue to database, restoring {len(items)} "
                f"item(s) to the queue: {e}"
            )
            for item in items:
                try:
                    self._queue.put_nowait(item)
                except asyncio.QueueFull:
                    logger.error("Queue full while restoring after failed flush; item dropped")

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
        """Add a data point to the queue. Returns False if the queue is full.

        Uses a non-blocking put: asyncio.Queue.put() *blocks* the caller when
        the queue is at capacity (it never raises QueueFull), which would stall
        the whole data-collection loop and silently starve everything after the
        put (StateStore updates, automation fan-out). put_nowait raises
        QueueFull instead, so an overflow drops the sample and returns False
        rather than hanging.
        """
        if "timestamp" not in data:
            data["timestamp"] = time.time()

        try:
            self._queue.put_nowait(data)
            logger.debug(f"Added item to queue, size: {self._queue.qsize()}")
            return True
        except asyncio.QueueFull:
            logger.warning("Queue is full, item dropped")
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

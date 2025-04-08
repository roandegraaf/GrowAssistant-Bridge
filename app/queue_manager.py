"""
Queue Manager Module.

This module provides a queue for buffering data before sending it to the API,
with persistence to ensure data is not lost if the application crashes.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.config import config


logger = logging.getLogger(__name__)


class QueueManager:
    """Manager for queuing data points with persistence.
    
    This class provides a queue for buffering data points before sending them to the API,
    with persistence to ensure data is not lost if the application crashes.
    
    Attributes:
        _instance: Singleton instance of the QueueManager.
        _queue: Asyncio queue for storing data points in memory.
        _db_conn: SQLite connection for persistence.
        _flush_task: Task that periodically flushes the queue to persistence.
    """
    
    _instance = None
    
    def __new__(cls):
        """Create or return the singleton instance.
        
        Returns:
            QueueManager: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(QueueManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the queue manager."""
        if self._initialized:
            return
            
        self._queue = asyncio.Queue(maxsize=config.get("queue.max_queue_size", 10000))
        self._db_conn = None
        self._flush_task = None
        self._initialized = True
        
        logger.info("Queue Manager initialized")
    
    async def start(self):
        """Start the queue manager.
        
        This method initializes the database connection and starts
        the background task for queue persistence.
        """
        if config.get("queue.persistence_enabled", True):
            self._init_db()
            self._load_from_db()
            
            # Start background task for periodic flushing
            self._flush_task = asyncio.create_task(self._periodic_flush())
            logger.info("Queue persistence started")
    
    async def stop(self):
        """Stop the queue manager.
        
        This method stops the background task and flushes the queue to persistence.
        """
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            
        # Final flush to persistence
        if config.get("queue.persistence_enabled", True):
            await self._flush_to_db()
            
            if self._db_conn:
                self._db_conn.close()
                self._db_conn = None
                
        logger.info("Queue Manager stopped")
    
    def _init_db(self):
        """Initialize the SQLite database connection."""
        db_file = config.get("queue.persistence_file", "data/queue.db")
        
        # Ensure the directory exists
        db_dir = os.path.dirname(db_file)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        self._db_conn = sqlite3.connect(db_file)
        
        # Create tables if they don't exist
        cursor = self._db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                data TEXT
            )
        ''')
        self._db_conn.commit()
        
        logger.info(f"Queue database initialized at {db_file}")
    
    def _load_from_db(self):
        """Load queued items from the database into memory."""
        if not self._db_conn:
            return
            
        cursor = self._db_conn.cursor()
        cursor.execute("SELECT id, timestamp, data FROM queue ORDER BY timestamp")
        rows = cursor.fetchall()
        
        count = 0
        for row in rows:
            try:
                id, timestamp, data_json = row
                data = json.loads(data_json)
                self._queue.put_nowait(data)
                count += 1
            except (json.JSONDecodeError, asyncio.QueueFull) as e:
                logger.error(f"Error loading item from queue database: {e}")
                
        if count > 0:
            logger.info(f"Loaded {count} items from queue database")
            
        # Clear the database after loading
        cursor.execute("DELETE FROM queue")
        self._db_conn.commit()
    
    async def _flush_to_db(self):
        """Flush the in-memory queue to the database."""
        if not self._db_conn:
            return
            
        count = 0
        cursor = self._db_conn.cursor()
        
        # Only flush if there are items in the queue
        if self._queue.empty():
            return
            
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                timestamp = item.get("timestamp", time.time())
                data_json = json.dumps(item)
                cursor.execute(
                    "INSERT INTO queue (timestamp, data) VALUES (?, ?)",
                    (timestamp, data_json)
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
        interval = config.get("queue.flush_interval", 300)  # Default: 5 minutes
        while True:
            try:
                await asyncio.sleep(interval)
                await self._flush_to_db()
            except asyncio.CancelledError:
                logger.info("Periodic flush task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic flush: {e}")
    
    async def put(self, data: Dict[str, Any]) -> bool:
        """Add a data point to the queue.
        
        Args:
            data: Data point to add to the queue.
            
        Returns:
            bool: True if the data point was added, False if the queue is full.
        """
        # Ensure timestamp is present
        if "timestamp" not in data:
            data["timestamp"] = time.time()
            
        try:
            await self._queue.put(data)
            logger.debug(f"Added item to queue, size: {self._queue.qsize()}")
            return True
        except asyncio.QueueFull:
            logger.warning("Queue is full, item not added")
            return False
    
    async def get(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Get a data point from the queue.
        
        Args:
            timeout: Timeout in seconds, or None to wait indefinitely.
            
        Returns:
            Optional[Dict[str, Any]]: Data point, or None if timeout occurs.
        """
        try:
            if timeout is None:
                item = await self._queue.get()
            else:
                item = await asyncio.wait_for(self._queue.get(), timeout)
                
            return item
        except asyncio.TimeoutError:
            return None
        
    async def get_batch(self, max_items: int, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Get a batch of data points from the queue.
        
        Args:
            max_items: Maximum number of items to get.
            timeout: Timeout in seconds, or None to return immediately available items.
            
        Returns:
            List[Dict[str, Any]]: List of data points.
        """
        items = []
        
        # Try to get the first item with timeout
        first_item = await self.get(timeout)
        if first_item:
            items.append(first_item)
        
        # Get remaining available items without waiting
        while len(items) < max_items and not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                items.append(item)
            except asyncio.QueueEmpty:
                break
                
        return items
    
    async def get_data_points(self, max_items: int, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Get a batch of data points from the queue - alias for get_batch.
        
        Args:
            max_items: Maximum number of items to get.
            timeout: Timeout in seconds, or None to return immediately available items.
            
        Returns:
            List[Dict[str, Any]]: List of data points.
        """
        return await self.get_batch(max_items, timeout)
    
    async def mark_processed(self, data_points: List[Dict[str, Any]]) -> None:
        """Mark data points as processed.
        
        Args:
            data_points: List of data points that were processed.
        """
        for _ in data_points:
            self.task_done()
        
        logger.debug(f"Marked {len(data_points)} items as processed")
    
    async def requeue_data_points(self, data_points: List[Dict[str, Any]]) -> None:
        """Requeue data points that failed to send.
        
        Args:
            data_points: List of data points to requeue.
        """
        count = 0
        for data_point in data_points:
            success = await self.put(data_point)
            if success:
                count += 1
            
        logger.info(f"Requeued {count}/{len(data_points)} data points")
    
    def task_done(self):
        """Mark a task as done."""
        self._queue.task_done()
        
    def size(self) -> int:
        """Get the current size of the queue.
        
        Returns:
            int: Number of items in the queue.
        """
        return self._queue.qsize()
    
    def is_empty(self) -> bool:
        """Check if the queue is empty.
        
        Returns:
            bool: True if the queue is empty, False otherwise.
        """
        return self._queue.empty()


# Create a global instance for easy imports
queue_manager = QueueManager() 
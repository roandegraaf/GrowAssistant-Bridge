"""Local config persistence using SQLite.

Stores configuration received via SSE for offline access and version tracking.
Also provides an outbound queue for requests that fail when the API is unreachable.
"""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from app.config import config
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DB_FILE = "data/config.db"


class ConfigStore(metaclass=SingletonMeta):
    """Local config persistence using SQLite.

    Stores bridge configuration and provides an outbound queue for
    requests that cannot be sent when the API is unreachable.
    """

    def __init__(self):
        """Initialize the config store."""
        self._db_conn: Optional[sqlite3.Connection] = None
        self._db_file = config.get("general.config_db_file", DEFAULT_CONFIG_DB_FILE)
        self._initialized = False
        logger.info("ConfigStore initialized")

    def start(self):
        """Start the config store and initialize the database."""
        if self._initialized:
            return

        db_dir = os.path.dirname(self._db_file)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db_conn = sqlite3.connect(self._db_file)
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS local_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                version INTEGER DEFAULT 0,
                updated_at REAL
            )
        """)
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS outbound_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL
            )
        """)
        self._db_conn.commit()
        self._initialized = True
        logger.info(f"ConfigStore database initialized at {self._db_file}")

    def stop(self):
        """Stop the config store and close the database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None
        self._initialized = False
        logger.info("ConfigStore stopped")

    def get_config_version(self) -> int:
        """Get the current config version from the store.

        Returns:
            The config version, or 0 if no config has been stored.
        """
        if not self._db_conn:
            return 0

        cursor = self._db_conn.execute("SELECT version FROM local_config WHERE key = ?", ("full",))
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_config(self, key: str) -> Optional[dict]:
        """Get a specific config entry by key.

        Args:
            key: The config key to retrieve.

        Returns:
            The config value as a dict, or None if not found.
        """
        if not self._db_conn:
            return None

        cursor = self._db_conn.execute("SELECT value FROM local_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                logger.error(f"Error decoding config for key '{key}'")
                return None
        return None

    def save_config(self, key: str, value: dict, version: int):
        """Save a config entry.

        Args:
            key: The config key.
            value: The config value as a dict.
            version: The config version.
        """
        if not self._db_conn:
            logger.warning("ConfigStore not started, cannot save config")
            return

        self._db_conn.execute(
            """INSERT OR REPLACE INTO local_config (key, value, version, updated_at)
               VALUES (?, ?, ?, ?)""",
            (key, json.dumps(value), version, time.time()),
        )
        self._db_conn.commit()
        logger.debug(f"Saved config key='{key}' version={version}")

    def save_full_config(self, config_data: dict, version: int):
        """Save the full config under the 'full' key.

        Args:
            config_data: The full config dict from the API.
            version: The config version.
        """
        self.save_config("full", config_data, version)
        logger.info(f"Saved full config version={version}")

    def get_full_config(self) -> tuple[Optional[dict], int]:
        """Get the full stored config and its version.

        Returns:
            A tuple of (config_dict, version). If no config is stored,
            returns (None, 0).
        """
        if not self._db_conn:
            return None, 0

        cursor = self._db_conn.execute(
            "SELECT value, version FROM local_config WHERE key = ?", ("full",)
        )
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0]), row[1]
            except (json.JSONDecodeError, TypeError):
                logger.error("Error decoding full config from store")
                return None, 0
        return None, 0

    # ─── Manifest version / hash ────────────────────────────────────

    def get_manifest_version(self) -> int:
        """Return the last accepted manifest version, or 0 if never sent."""
        if not self._db_conn:
            return 0
        cursor = self._db_conn.execute(
            "SELECT value FROM local_config WHERE key = ?", ("manifest_version",)
        )
        row = cursor.fetchone()
        if not row:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            logger.error("Invalid manifest_version in store, resetting to 0")
            return 0

    def set_manifest_version(self, version: int) -> None:
        """Persist the latest accepted manifest version."""
        if not self._db_conn:
            logger.warning("ConfigStore not started, cannot save manifest_version")
            return
        self._db_conn.execute(
            """INSERT OR REPLACE INTO local_config (key, value, version, updated_at)
               VALUES (?, ?, ?, ?)""",
            ("manifest_version", str(int(version)), int(version), time.time()),
        )
        self._db_conn.commit()
        logger.debug(f"Saved manifest_version={version}")

    def get_manifest_hash(self) -> Optional[str]:
        """Return the hash of the last successfully-pushed manifest, or None."""
        if not self._db_conn:
            return None
        cursor = self._db_conn.execute(
            "SELECT value FROM local_config WHERE key = ?", ("manifest_hash",)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def set_manifest_hash(self, manifest_hash: str) -> None:
        """Persist the hash of the last successfully-pushed manifest."""
        if not self._db_conn:
            logger.warning("ConfigStore not started, cannot save manifest_hash")
            return
        self._db_conn.execute(
            """INSERT OR REPLACE INTO local_config (key, value, version, updated_at)
               VALUES (?, ?, ?, ?)""",
            ("manifest_hash", manifest_hash, 0, time.time()),
        )
        self._db_conn.commit()
        logger.debug(f"Saved manifest_hash={manifest_hash[:12]}…")

    # ─── Device assignments (display-only, from SSE config event) ──

    def save_device_assignments(self, assignments: list[dict], version: int) -> None:
        """Persist the latest device assignments list from the API.

        Stored under the ``device_assignments`` key via the existing
        ``save_config`` mechanism. The payload is the raw list of
        ``{entityId, role, slot}`` dicts as received over SSE; this is
        used solely by the bridge web UI for labeling. Command routing
        never consults this list.
        """
        # save_config uses json.dumps which round-trips lists fine, but
        # the type hint says ``dict``. Wrapping happens at the SQL layer
        # via json serialization, so a top-level list works in practice.
        self.save_config("device_assignments", assignments, version)
        logger.info(f"Saved device_assignments version={version} count={len(assignments)}")

    def get_device_assignments(self) -> list[dict]:
        """Return the stored device assignments, or [] if none stored.

        Mirrors the read pattern of ``get_full_config`` but returns just
        the list (no version pair), since callers only need the data.
        """
        if not self._db_conn:
            return []

        cursor = self._db_conn.execute(
            "SELECT value FROM local_config WHERE key = ?", ("device_assignments",)
        )
        row = cursor.fetchone()
        if not row:
            return []
        try:
            data = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            logger.error("Error decoding device_assignments from store")
            return []
        if not isinstance(data, list):
            logger.warning(
                "Stored device_assignments is not a list (got %s); returning []",
                type(data).__name__,
            )
            return []
        return data

    def queue_outbound(self, endpoint: str, payload: dict):
        """Queue an outbound request for later delivery.

        Args:
            endpoint: The API endpoint path.
            payload: The request payload as a dict.
        """
        if not self._db_conn:
            logger.warning("ConfigStore not started, cannot queue outbound request")
            return

        self._db_conn.execute(
            "INSERT INTO outbound_queue (endpoint, payload, created_at) VALUES (?, ?, ?)",
            (endpoint, json.dumps(payload), time.time()),
        )
        self._db_conn.commit()
        logger.debug(f"Queued outbound request to {endpoint}")

    def get_pending_outbound(self) -> list[tuple[int, str, dict]]:
        """Get all pending outbound requests.

        Returns:
            A list of (row_id, endpoint, payload) tuples.
        """
        if not self._db_conn:
            return []

        cursor = self._db_conn.execute(
            "SELECT id, endpoint, payload FROM outbound_queue ORDER BY created_at"
        )
        results = []
        for row_id, endpoint, payload_json in cursor.fetchall():
            try:
                payload = json.loads(payload_json)
                results.append((row_id, endpoint, payload))
            except (json.JSONDecodeError, TypeError):
                logger.error(f"Error decoding outbound payload id={row_id}")
        return results

    def remove_outbound(self, row_id: int):
        """Remove an outbound request after successful delivery.

        Args:
            row_id: The row ID of the outbound request to remove.
        """
        if not self._db_conn:
            return

        self._db_conn.execute("DELETE FROM outbound_queue WHERE id = ?", (row_id,))
        self._db_conn.commit()
        logger.debug(f"Removed outbound request id={row_id}")


# Create a global instance for easy imports
config_store = ConfigStore()

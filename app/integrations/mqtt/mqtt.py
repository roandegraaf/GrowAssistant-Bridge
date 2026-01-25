"""
MQTT Integration Implementation.

This module provides the MQTTIntegration class for interacting with MQTT brokers.
It uses the paho-mqtt library for MQTT protocol implementation.
"""

import asyncio
import json
import logging
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

from app.integrations import Integration, register_integration
from app.schemas.config_schemas import MQTTIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@register_integration
class MQTTIntegration(Integration):
    """Integration for MQTT communication."""

    CONFIG_SCHEMA = MQTTIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        """Initialize the MQTT integration.

        Args:
            config: Configuration dictionary for MQTT integration.
        """
        super().__init__(config)
        self.client: mqtt.Client | None = None
        self.connected = False
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.topics: dict[str, dict[str, str]] = {}
        self._latest_device_data: dict[str, dict[str, Any]] = {}

        if not self.config.get("enabled", False):
            logger.info("MQTT Integration is disabled in configuration.")
            return

        topic_configs = self.config.get("topics", {})
        if not topic_configs:
            logger.warning("No MQTT topics configured.")
            return

        for topic_config in topic_configs.values():
            if not isinstance(topic_config, dict):
                logger.error(f"Invalid topic configuration: {topic_config}")
                continue

            name = topic_config.get("name")
            topic_type = topic_config.get("type")

            if not name or not topic_type:
                logger.error(f"Invalid topic configuration: {topic_config}")
                continue

            self.topics[name] = {"name": name, "type": topic_type}

        self.broker = self.config.get("broker", "localhost")
        self.port = self.config.get("port", 1883)
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
        self.client_id = self.config.get("client_id", "grow_assistant")

        logger.info(
            f"MQTT Integration initialized with broker {self.broker}:{self.port} "
            f"and {len(self.topics)} topics"
        )

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connection to MQTT broker is established."""
        if rc != 0:
            logger.error(f"Failed to connect to MQTT broker, return code: {rc}")
            self.connected = False
            return

        logger.info(f"Connected to MQTT broker {self.broker}:{self.port}")
        self.connected = True

        for topic_name in self.topics:
            client.subscribe(topic_name)
            logger.debug(f"Subscribed to topic: {topic_name}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        logger.info("Disconnected from MQTT broker")
        self.connected = False

    def _on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker."""
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8")
            timestamp = time.time()

            try:
                payload_data = json.loads(payload)
            except json.JSONDecodeError:
                payload_data = {"value": payload}

            message_data = {"topic": topic, "timestamp": timestamp, "data": payload_data}

            topic_info = self.topics.get(topic)
            if topic_info:
                message_data["type"] = topic_info.get("type")
                self._latest_device_data[topic] = {
                    "data": payload_data,
                    "timestamp": timestamp,
                }

            asyncio.run_coroutine_threadsafe(
                self.message_queue.put(message_data), asyncio.get_event_loop()
            )

            logger.debug(f"Received message on topic {topic}: {payload}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    async def connect(self) -> bool:
        """Connect to the MQTT broker.

        Returns:
            bool: True if connection was successful, False otherwise.
        """
        if not self.config.get("enabled", False):
            return False

        try:
            self.client = mqtt.Client(client_id=self.client_id)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)

            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()

            for _ in range(10):
                if self.connected:
                    return True
                await asyncio.sleep(0.5)

            logger.error(f"Timed out connecting to MQTT broker {self.broker}:{self.port}")
            return False

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Publish a message to an MQTT topic.

        Args:
            data: Dictionary containing:
                - topic: The topic to publish to
                - payload: The message payload (will be JSON serialized)

        Returns:
            bool: True if publish was successful, False otherwise.
        """
        if not self.connected or not self.client:
            logger.error("MQTT not connected. Cannot send data.")
            return False

        topic = data.get("topic")
        payload = data.get("payload")

        if not topic or payload is None:
            logger.error(f"Invalid MQTT data: {data}")
            return False

        try:
            payload_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
            result = self.client.publish(topic, payload_str)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published message to {topic}: {payload_str}")
                return True

            logger.error(f"Failed to publish message to {topic}, error code: {result.rc}")
            return False

        except Exception as e:
            logger.error(f"Failed to publish MQTT message: {e}")
            return False

    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        """Receive messages from subscribed MQTT topics.

        Yields:
            Dict[str, Any]: Data received from MQTT topics.
        """
        if not self.connected or not self.client:
            logger.error("MQTT not connected. Cannot receive data.")
            return

        while not self.message_queue.empty():
            try:
                message = self.message_queue.get_nowait()
                yield message
                self.message_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def get_device_data(self) -> dict[str, Any]:
        """Get the current data/state for all MQTT devices (topics).

        Returns:
            Dict[str, Any]: Dictionary mapping device names to their last received data.
        """
        return self._latest_device_data.copy()

    async def disconnect(self):
        """Disconnect from the MQTT broker and clean up."""
        if not self.client:
            logger.error("MQTT client is not connected. Cannot disconnect.")
            return

        try:
            self.client.loop_stop()
            self.client.disconnect()
            logger.debug("MQTT client disconnected and stopped.")
        except Exception as e:
            logger.error(f"Failed to clean up MQTT resources: {e}")

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register MQTT topic capabilities with the device registry.

        Args:
            registry: The DeviceRegistry instance to register with.
        """
        for topic_name, topic_info in self.topics.items():
            topic_type = topic_info.get("type")
            if not topic_type:
                continue

            is_actuator = topic_name.startswith("controls/")

            if is_actuator:
                registry.register_actuator(
                    actuator_name=topic_type,
                    integration_name=self.name,
                    domain="mqtt",
                    device_type=topic_type,
                )
            else:
                registry.register_sensor(
                    sensor_name=topic_type,
                    integration_name=self.name,
                    domain="mqtt",
                    device_type=topic_type,
                )

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command via MQTT.

        Args:
            target_id: The target device/topic identifier.
            action: The action to perform.
            payload: Additional command parameters.

        Returns:
            bool: True if successful.
        """
        return await self.send_data(
            {
                "topic": f"controls/{target_id}",
                "payload": {"action": action, **payload},
            }
        )

    def __del__(self):
        """Clean up MQTT resources when the object is destroyed."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
                logger.debug("MQTT client disconnected and stopped.")
            except Exception as e:
                logger.error(f"Failed to clean up MQTT resources: {e}")

"""
MQTT Integration Implementation.

This module provides the MQTTIntegration class for interacting with MQTT brokers.
It uses the paho-mqtt library for MQTT protocol implementation.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Generator, List, Optional

import paho.mqtt.client as mqtt

from app.integrations import Integration, register_integration

logger = logging.getLogger(__name__)


@register_integration
class MQTTIntegration(Integration):
    """Integration for MQTT communication."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the MQTT integration.
        
        Args:
            config: Configuration dictionary for MQTT integration.
        """
        super().__init__(config)
        self.client = None
        self.connected = False
        self.message_queue = asyncio.Queue()
        self.topics = {}
        self._latest_device_data: Dict[str, Dict[str, Any]] = {} # Store latest data per device name
        
        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("MQTT Integration is disabled in configuration.")
            return
        
        # Parse topic configurations
        topic_configs = self.config.get("topics", {})
        if not topic_configs:
            logger.warning("No MQTT topics configured.")
            return
            
        # Process each topic configuration
        for _, topic_config in topic_configs.items():
            # Skip if not a dictionary (might be a string or other type)
            if not isinstance(topic_config, dict):
                logger.error(f"Invalid topic configuration: {topic_config}")
                continue
                
            name = topic_config.get("name")
            topic_type = topic_config.get("type")
            
            if not name or not topic_type:
                logger.error(f"Invalid topic configuration: {topic_config}")
                continue
                
            self.topics[name] = {
                "name": name,
                "type": topic_type
            }
            
        self.broker = self.config.get("broker", "localhost")
        self.port = self.config.get("port", 1883)
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
        self.client_id = self.config.get("client_id", "grow_assistant")
        
        logger.info(f"MQTT Integration initialized with broker {self.broker}:{self.port} and {len(self.topics)} topics")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connection to MQTT broker is established.
        
        Args:
            client: MQTT client instance
            userdata: User data
            flags: Connection flags
            rc: Connection result code
        """
        if rc == 0:
            logger.info(f"Connected to MQTT broker {self.broker}:{self.port}")
            self.connected = True
            
            # Subscribe to topics
            for topic_name, topic_info in self.topics.items():
                client.subscribe(topic_name)
                logger.debug(f"Subscribed to topic: {topic_name}")
        else:
            logger.error(f"Failed to connect to MQTT broker, return code: {rc}")
            self.connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker.
        
        Args:
            client: MQTT client instance
            userdata: User data
            rc: Disconnect result code
        """
        logger.info("Disconnected from MQTT broker")
        self.connected = False
        
    def _on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker.
        
        Args:
            client: MQTT client instance
            userdata: User data
            msg: Received message
        """
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8")
            
            # Try to parse as JSON
            try:
                payload_data = json.loads(payload)
            except json.JSONDecodeError:
                payload_data = {"value": payload}
            
            # Add topic info to payload
            message_data = {
                "topic": topic,
                "timestamp": time.time(),
                "data": payload_data
            }
            
            # Find topic name associated with the received topic string
            matched_topic_name = None
            for topic_name, topic_info in self.topics.items():
                # Simple exact match for now, could support wildcards later if needed
                if topic == topic_name:
                    message_data["type"] = topic_info.get("type")
                    matched_topic_name = topic_name
                    break
            
            # Store latest data if it corresponds to a configured device
            if matched_topic_name:
                self._latest_device_data[matched_topic_name] = {
                    "data": payload_data,
                    "timestamp": message_data["timestamp"]
                }
            
            # Put message in queue for async processing
            asyncio.run_coroutine_threadsafe(
                self.message_queue.put(message_data), 
                asyncio.get_event_loop()
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
            # Create MQTT client
            self.client = mqtt.Client(client_id=self.client_id)
            
            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            
            # Set authentication if provided
            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)
                
            # Connect to broker
            self.client.connect(self.broker, self.port, keepalive=60)
            
            # Start network loop in a background thread
            self.client.loop_start()
            
            # Wait for connection to be established
            for _ in range(10):  # Wait up to 5 seconds
                if self.connected:
                    return True
                await asyncio.sleep(0.5)
                
            # If we get here, connection failed
            logger.error(f"Timed out connecting to MQTT broker {self.broker}:{self.port}")
            return False
                
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
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
            # Convert payload to JSON string if it's a dict or list
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload)
            else:
                payload_str = str(payload)
                
            # Publish message
            result = self.client.publish(topic, payload_str)
            
            # Check for success
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published message to {topic}: {payload_str}")
                return True
            else:
                logger.error(f"Failed to publish message to {topic}, error code: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to publish MQTT message: {e}")
            return False
            
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Receive messages from subscribed MQTT topics.
        
        Yields:
            Dict[str, Any]: Data received from MQTT topics.
        """
        if not self.connected or not self.client:
            logger.error("MQTT not connected. Cannot receive data.")
            return
            
        # Get all messages currently in the queue
        while not self.message_queue.empty():
            try:
                message = self.message_queue.get_nowait()
                yield message
                self.message_queue.task_done()
            except asyncio.QueueEmpty:
                break
                
    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data/state for all MQTT devices (topics).

        Returns:
            Dict[str, Any]: Dictionary mapping device names to their last received data.
        """
        # Return a copy to prevent external modification
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
    
    def __del__(self):
        """Clean up MQTT resources when the object is destroyed."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
                logger.debug("MQTT client disconnected and stopped.")
            except Exception as e:
                logger.error(f"Failed to clean up MQTT resources: {e}") 
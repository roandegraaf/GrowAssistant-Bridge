"""
HTTP Integration Implementation.

This module provides the HTTPIntegration class for interacting with HTTP APIs.
It uses the httpx library for HTTP requests.
"""

import asyncio
import json
import logging
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import httpx

from app.integrations import Integration, register_integration
from app.schemas.config_schemas import HTTPIntegrationConfig

if TYPE_CHECKING:
    from app.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@register_integration
class HTTPIntegration(Integration):
    """Integration for HTTP communication."""

    CONFIG_SCHEMA = HTTPIntegrationConfig

    def __init__(self, config: dict[str, Any]):
        """Initialize the HTTP integration.

        Args:
            config: Configuration dictionary for HTTP integration.
        """
        super().__init__(config)
        self.client: httpx.AsyncClient | None = None
        self.endpoints: dict[str, dict[str, Any]] = {}
        self.poll_tasks: dict[str, asyncio.Task] = {}

        if not self.config.get("enabled", False):
            logger.info("HTTP Integration is disabled in configuration.")
            return

        endpoint_configs = self.config.get("endpoints", {})
        if not endpoint_configs:
            logger.warning("No HTTP endpoints configured.")
            return

        for endpoint_config in endpoint_configs.values():
            if not isinstance(endpoint_config, dict):
                logger.error(f"Invalid endpoint configuration: {endpoint_config}")
                continue

            name = endpoint_config.get("name")
            url = endpoint_config.get("url")

            if not name or not url:
                logger.error(f"Invalid endpoint configuration: {endpoint_config}")
                continue

            self.endpoints[name] = {
                "url": url,
                "method": endpoint_config.get("method", "GET"),
                "headers": endpoint_config.get("headers", {}),
                "interval": endpoint_config.get("interval", 300),
                "value_key": endpoint_config.get("value_key"),
            }

        logger.info(f"HTTP Integration initialized with {len(self.endpoints)} endpoints")

    async def connect(self) -> bool:
        """Initialize the HTTP client.

        Returns:
            bool: True if initialization was successful, False otherwise.
        """
        if not self.config.get("enabled", False):
            return False

        try:
            self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

            for name, endpoint in self.endpoints.items():
                interval = endpoint.get("interval", 0)
                is_get = endpoint["method"].upper() == "GET"
                if interval > 0 and is_get:
                    self.poll_tasks[name] = asyncio.create_task(self._poll_endpoint(name, endpoint))

            logger.info("HTTP Integration connected successfully.")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize HTTP client: {e}")
            return False

    async def _poll_endpoint(self, name: str, endpoint: dict[str, Any]):
        """Periodically poll an HTTP endpoint.

        Args:
            name: Name of the endpoint
            endpoint: Endpoint configuration
        """
        interval = endpoint.get("interval", 300)

        while True:
            try:
                logger.debug(f"Polling HTTP endpoint: {name}")

                response = await self.client.request(
                    method=endpoint["method"],
                    url=endpoint["url"],
                    headers=endpoint["headers"],
                )

                timestamp = time.time()
                if 200 <= response.status_code < 300:
                    try:
                        data = response.json()
                    except json.JSONDecodeError:
                        data = {"text": response.text}

                    self.endpoints[name]["last_poll_result"] = {
                        "timestamp": timestamp,
                        "status_code": response.status_code,
                        "data": data,
                    }
                    logger.debug(f"Successfully polled endpoint {name}: {response.status_code}")
                else:
                    logger.error(f"Failed to poll endpoint {name}: {response.status_code}")
                    self.endpoints[name]["last_poll_result"] = {
                        "timestamp": timestamp,
                        "status_code": response.status_code,
                        "error": f"HTTP error: {response.status_code}",
                    }

            except Exception as e:
                logger.error(f"Error polling endpoint {name}: {e}")
                self.endpoints[name]["last_poll_result"] = {
                    "timestamp": time.time(),
                    "error": str(e),
                }

            await asyncio.sleep(interval)

    async def send_data(self, data: dict[str, Any]) -> bool:
        """Send data to an HTTP endpoint.

        Args:
            data: Dictionary containing:
                - endpoint_name: Name of the endpoint to use (optional)
                - url: URL to send data to (optional, overrides endpoint_name)
                - method: HTTP method (optional, defaults to POST)
                - headers: HTTP headers (optional)
                - payload: Data to send

        Returns:
            bool: True if the request was successful, False otherwise.
        """
        if not self.client:
            logger.error("HTTP client not initialized. Cannot send data.")
            return False

        endpoint_name = data.get("endpoint_name")
        url = data.get("url")
        payload = data.get("payload")

        if not endpoint_name and not url:
            logger.error("No endpoint_name or url provided in HTTP data")
            return False

        if payload is None:
            logger.error("No payload provided in HTTP data")
            return False

        try:
            if endpoint_name:
                if endpoint_name not in self.endpoints:
                    logger.error(f"Unknown HTTP endpoint: {endpoint_name}")
                    return False

                endpoint = self.endpoints[endpoint_name]
                url = endpoint["url"]
                method = endpoint["method"]
                headers = endpoint["headers"].copy()
                if "headers" in data:
                    headers.update(data["headers"])
            else:
                method = data.get("method", "POST")
                headers = data.get("headers", {})

            json_data = payload if isinstance(payload, (dict, list)) else None
            content = None if json_data else payload

            response = await self.client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                content=content,
            )

            if 200 <= response.status_code < 300:
                logger.debug(f"Successfully sent data to {url}: {response.status_code}")
                return True

            logger.error(f"Failed to send data to {url}: {response.status_code}")
            return False

        except Exception as e:
            logger.error(f"Failed to send HTTP request: {e}")
            return False

    @staticmethod
    def _extract_value(data: Any, value_key: str | None) -> Any:
        """Pull the telemetry value out of a polled response body.

        An explicit ``value_key`` (dot-path, e.g. ``data.temperature``) wins;
        otherwise scalars are taken as-is and dicts are probed for the
        conventional ``value``/``state`` keys. Returns None when nothing
        usable is found.
        """
        if value_key:
            current = data
            for part in value_key.split("."):
                if not isinstance(current, dict):
                    return None
                current = current.get(part)
            return current if isinstance(current, (str, int, float, bool)) else None
        if isinstance(data, (str, int, float, bool)):
            return data
        if isinstance(data, dict):
            for key in ("value", "state"):
                if data.get(key) is not None:
                    return data[key]
        return None

    async def receive_data(self) -> Generator[dict[str, Any], None, None]:
        """Get data from HTTP endpoints that have been polled.

        Yields telemetry-contract samples: explicit ``entity_id`` matching the
        registered ``http.<endpoint_name>`` entity, with the reading extracted
        to a top-level ``value``. Failed polls are not yielded (an error has
        no value to join).
        """
        for name, endpoint in self.endpoints.items():
            result = endpoint.get("last_poll_result")
            if not result:
                continue

            last_yielded = endpoint.get("last_result_yielded", 0)
            if last_yielded >= result["timestamp"]:
                continue
            endpoint["last_result_yielded"] = result["timestamp"]

            if result.get("error"):
                logger.debug(f"Skipping errored poll result for endpoint {name}")
                continue

            value = self._extract_value(result.get("data"), endpoint.get("value_key"))
            if value is None:
                logger.warning(
                    f"HTTP endpoint {name} response has no usable value "
                    f"(configure value_key for this endpoint?)"
                )

            yield self.telemetry_sample(
                name,
                value,
                domain="http",
                url=endpoint["url"],
                timestamp=result["timestamp"],
                data=result.get("data"),
                status_code=result.get("status_code"),
            )

    async def get_device_data(self) -> dict[str, Any]:
        """Get the current data/state for all HTTP endpoints (devices).

        Returns:
            Dict[str, Any]: Dictionary mapping endpoint names to their last polled data or error.
        """
        device_data = {}
        for name, endpoint in self.endpoints.items():
            result = endpoint.get("last_poll_result")
            if not result:
                device_data[name] = {"status": "pending"}
                continue

            if "error" in result:
                device_data[name] = {
                    "error": result["error"],
                    "timestamp": result.get("timestamp"),
                }
            else:
                device_data[name] = {
                    "value": self._extract_value(result.get("data"), endpoint.get("value_key")),
                    "data": result.get("data"),
                    "timestamp": result.get("timestamp"),
                }

        return device_data

    async def close(self):
        """Close HTTP resources."""
        for task in self.poll_tasks.values():
            task.cancel()

        if self.client:
            await self.client.aclose()
            logger.debug("HTTP client closed.")

    def register_capabilities(self, registry: "DeviceRegistry") -> None:
        """Register HTTP endpoint capabilities with the device registry.

        Args:
            registry: The DeviceRegistry instance to register with.
        """
        for endpoint_name, endpoint_config in self.endpoints.items():
            method = endpoint_config.get("method", "GET").upper()

            if method == "GET":
                registry.register_sensor(
                    sensor_name=endpoint_name,
                    integration_name=self.name,
                    domain="http",
                    device_type="http_endpoint",
                )
            else:
                registry.register_actuator(
                    actuator_name=endpoint_name,
                    integration_name=self.name,
                    domain="http",
                    device_type="http_endpoint",
                )

    async def execute_command(self, target_id: str, action: str, payload: dict[str, Any]) -> bool:
        """Execute a command via HTTP.

        Args:
            target_id: The endpoint name.
            action: The action to perform.
            payload: Additional command parameters.

        Returns:
            bool: True if successful.
        """
        return await self.send_data(
            {
                "endpoint_name": target_id,
                "payload": {"action": action, **payload},
            }
        )

    def __del__(self):
        """Clean up HTTP resources when the object is destroyed."""
        if self.client:
            try:
                asyncio.create_task(self.close())
            except Exception as e:
                logger.error(f"Failed to clean up HTTP resources: {e}")

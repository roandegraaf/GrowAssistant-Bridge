"""
HTTP Integration Implementation.

This module provides the HTTPIntegration class for interacting with HTTP APIs.
It uses the httpx library for HTTP requests.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Generator, List, Optional

import httpx

from app.integrations import Integration, register_integration

logger = logging.getLogger(__name__)


@register_integration
class HTTPIntegration(Integration):
    """Integration for HTTP communication."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the HTTP integration.
        
        Args:
            config: Configuration dictionary for HTTP integration.
        """
        super().__init__(config)
        self.client = None
        self.endpoints = {}
        self.poll_tasks = {}
        
        # Check if enabled
        if not self.config.get("enabled", False):
            logger.info("HTTP Integration is disabled in configuration.")
            return
        
        # Parse endpoint configurations
        endpoint_configs = self.config.get("endpoints", {})
        if not endpoint_configs:
            logger.warning("No HTTP endpoints configured.")
            return
            
        # Process each endpoint configuration
        for _, endpoint_config in endpoint_configs.items():
            # Skip if not a dictionary (might be a string or other type)
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
                "interval": endpoint_config.get("interval", 300)  # Default to 5 minutes
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
            # Create HTTP client
            self.client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True
            )
            
            # Start polling tasks for endpoints with interval
            for name, endpoint in self.endpoints.items():
                if "interval" in endpoint and endpoint["interval"] > 0:
                    # Only start polling for GET endpoints
                    if endpoint["method"].upper() == "GET":
                        self.poll_tasks[name] = asyncio.create_task(
                            self._poll_endpoint(name, endpoint)
                        )
            
            logger.info("HTTP Integration connected successfully.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize HTTP client: {e}")
            return False
    
    async def _poll_endpoint(self, name: str, endpoint: Dict[str, Any]):
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
                    headers=endpoint["headers"]
                )
                
                if response.status_code >= 200 and response.status_code < 300:
                    # Try to parse as JSON
                    try:
                        data = response.json()
                    except json.JSONDecodeError:
                        data = {"text": response.text}
                        
                    # Put data in polling result
                    self.endpoints[name]["last_poll_result"] = {
                        "timestamp": time.time(),
                        "status_code": response.status_code,
                        "data": data
                    }
                    
                    logger.debug(f"Successfully polled endpoint {name}: {response.status_code}")
                else:
                    logger.error(f"Failed to poll endpoint {name}: {response.status_code}")
                    self.endpoints[name]["last_poll_result"] = {
                        "timestamp": time.time(),
                        "status_code": response.status_code,
                        "error": f"HTTP error: {response.status_code}"
                    }
            except Exception as e:
                logger.error(f"Error polling endpoint {name}: {e}")
                self.endpoints[name]["last_poll_result"] = {
                    "timestamp": time.time(),
                    "error": str(e)
                }
                
            # Wait for next interval
            await asyncio.sleep(interval)
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
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
            # If endpoint_name is provided, use its configuration
            if endpoint_name:
                if endpoint_name not in self.endpoints:
                    logger.error(f"Unknown HTTP endpoint: {endpoint_name}")
                    return False
                    
                endpoint = self.endpoints[endpoint_name]
                url = endpoint["url"]
                method = endpoint["method"]
                headers = endpoint["headers"].copy()
            else:
                # Otherwise use provided values or defaults
                method = data.get("method", "POST")
                headers = data.get("headers", {})
                
            # Merge provided headers with endpoint headers if endpoint_name was used
            if "headers" in data and endpoint_name:
                headers.update(data["headers"])
                
            # Convert payload to JSON if it's a dict or list
            if isinstance(payload, (dict, list)):
                json_data = payload
                payload = None
            else:
                json_data = None
                
            # Send request
            response = await self.client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                content=payload if json_data is None else None
            )
            
            # Check for success
            if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"Successfully sent data to {url}: {response.status_code}")
                return True
            else:
                logger.error(f"Failed to send data to {url}: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send HTTP request: {e}")
            return False
            
    async def receive_data(self) -> Generator[Dict[str, Any], None, None]:
        """Get data from HTTP endpoints that have been polled.
        
        Yields:
            Dict[str, Any]: Data received from polled HTTP endpoints.
        """
        for name, endpoint in self.endpoints.items():
            if "last_poll_result" in endpoint:
                result = endpoint["last_poll_result"]
                
                # Only yield results that haven't been yielded yet
                if not endpoint.get("last_result_yielded") or endpoint["last_result_yielded"] < result["timestamp"]:
                    yield {
                        "endpoint_name": name,
                        "url": endpoint["url"],
                        "timestamp": result["timestamp"],
                        "data": result.get("data"),
                        "status_code": result.get("status_code"),
                        "error": result.get("error")
                    }
                    
                    # Mark as yielded
                    endpoint["last_result_yielded"] = result["timestamp"]
    
    async def get_device_data(self) -> Dict[str, Any]:
        """Get the current data/state for all HTTP endpoints (devices).

        Returns:
            Dict[str, Any]: Dictionary mapping endpoint names to their last polled data or error.
        """
        device_data = {}
        for name, endpoint in self.endpoints.items():
            if "last_poll_result" in endpoint:
                result = endpoint["last_poll_result"]
                if "error" in result:
                    device_data[name] = {"error": result["error"], "timestamp": result.get("timestamp")}
                else:
                    device_data[name] = {"data": result.get("data"), "timestamp": result.get("timestamp")}
            else:
                # Endpoint might not have been polled yet or polling is disabled
                device_data[name] = {"status": "pending"}
        return device_data
    
    async def close(self):
        """Close HTTP resources."""
        # Cancel all polling tasks
        for name, task in self.poll_tasks.items():
            task.cancel()
            
        # Close HTTP client
        if self.client:
            await self.client.aclose()
            logger.debug("HTTP client closed.")
    
    def __del__(self):
        """Clean up HTTP resources when the object is destroyed."""
        if self.client:
            try:
                asyncio.create_task(self.close())
            except Exception as e:
                logger.error(f"Failed to clean up HTTP resources: {e}") 
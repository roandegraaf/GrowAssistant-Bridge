"""
Authentication Module.

This module provides functionality for authenticating with the GrowAssistant Spring API.
It handles client registration, authentication code display, and credential management.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple, Literal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import config


logger = logging.getLogger(__name__)


class AuthManager:
    """Authentication manager for the GrowAssistant Spring API.
    
    This class handles client registration, authentication, and credential management.
    
    Attributes:
        _instance: Singleton instance of the AuthManager.
        _client: HTTP client for making requests.
        _base_url: Base URL of the API.
        _credentials: Authentication credentials.
        _credentials_file: Path to the credentials file.
        _auth_code: Authentication code for connecting client to environment.
        _client_id: The client ID for API authentication.
    """
    
    _instance = None
    
    def __new__(cls):
        """Create or return the singleton instance.
        
        Returns:
            AuthManager: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(AuthManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the authentication manager."""
        if self._initialized:
            return
            
        self._client: Optional[httpx.AsyncClient] = None
        self._base_url = config.get("api.url", "http://localhost:8080")
        
        # Credentials storage
        data_dir = config.get("general.data_dir", "data")
        os.makedirs(data_dir, exist_ok=True)
        self._credentials_file = os.path.join(data_dir, "credentials.json")
        
        # State variables
        self._credentials = None
        self._auth_code = None
        self._client_id = None
        self._initialized = True
        
        logger.info("Authentication manager initialized")
    
    async def start(self):
        """Start the authentication manager.
        
        This initializes the HTTP client and loads saved credentials.
        """
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        
        # Load saved credentials
        self._load_credentials()
        
        logger.info("Authentication manager started")
    
    async def stop(self):
        """Stop the authentication manager."""
        if self._client:
            await self._client.aclose()
            self._client = None
            
        logger.info("Authentication manager stopped")
    
    def _load_credentials(self) -> bool:
        """Load saved credentials from file.
        
        Returns:
            bool: True if credentials were loaded successfully, False otherwise.
        """
        if not os.path.exists(self._credentials_file):
            logger.info("No saved credentials found")
            return False
            
        try:
            with open(self._credentials_file, "r") as f:
                self._credentials = json.load(f)
                
            self._client_id = self._credentials.get("client_id")
            
            logger.info(f"Loaded credentials for client ID: {self._client_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")
            return False
    
    def _save_credentials(self) -> bool:
        """Save credentials to file.
        
        Returns:
            bool: True if credentials were saved successfully, False otherwise.
        """
        if not self._credentials:
            logger.warning("No credentials to save")
            return False
            
        try:
            with open(self._credentials_file, "w") as f:
                json.dump(self._credentials, f)
                
            logger.info(f"Saved credentials for client ID: {self._client_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving credentials: {e}")
            return False
    
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated.
        
        Returns:
            bool: True if authenticated, False otherwise.
        """
        return self._credentials is not None and self._client_id is not None
    
    async def validate_credentials(self) -> bool:
        """Validate saved credentials with the API.
        
        Returns:
            bool: True if credentials are valid, False otherwise.
        """
        if not self.is_authenticated() or not self._client:
            logger.warning("Not authenticated or client not started")
            return False
            
        url = f"{self._base_url}/client/{self._client_id}"
        
        try:
            response = await self._client.get(
                url,
                headers=self._get_auth_headers(),
            )
            
            if response.status_code == 200:
                logger.info("Credentials validated successfully")
                return True
                
            logger.warning(f"Credentials validation failed: {response.status_code}")
            return False
            
        except Exception as e:
            logger.error(f"Error validating credentials: {e}")
            return False
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests.
        
        Returns:
            Dict[str, str]: Authentication headers.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        if self._credentials and "token" in self._credentials:
            headers["Authorization"] = f"Bearer {self._credentials['token']}"
            
        return headers
    
    async def register_client(self) -> bool:
        """Register a new client with the API.
        
        This method generates a custom ID and registers a new client with the API.
        It receives and stores the authentication code that the user will enter in the app.
        
        Returns:
            bool: True if registration successful, False otherwise.
        """
        if not self._client:
            logger.error("Authentication manager not started")
            return False
            
        # Generate a custom ID
        custom_id = self._generate_custom_id()
        
        url = f"{self._base_url}/client"
        payload = {"customId": custom_id}
        
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
                stop=stop_after_attempt(config.get("api.retry_max_attempts", 5)),
                wait=wait_exponential(
                    min=config.get("api.retry_min_backoff", 1),
                    max=config.get("api.retry_max_backoff", 60),
                ),
            ):
                with attempt:
                    logger.info(f"Registering client with customId: {custom_id}")
                    response = await self._client.post(url, json=payload)
                    response.raise_for_status()
                    
            response_data = response.json()
            
            # Store the client ID and authentication code
            self._client_id = response_data.get("id")
            self._auth_code = response_data.get("code")
            
            # Store temporary credentials
            self._credentials = {
                "client_id": self._client_id,
                "custom_id": custom_id,
                "registration_time": str(
                    asyncio.get_event_loop().time()
                ),
            }
            
            # Save the credentials
            self._save_credentials()
            
            logger.info(f"Client registered successfully with ID: {self._client_id}")
            logger.info(f"Authentication code: {self._auth_code}")
            
            return True
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error registering client: {e.response.status_code} - {e.response.text}")
            return False
            
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            logger.error(f"Error registering client: {str(e)}")
            return False
            
        except Exception as e:
            logger.exception(f"Unexpected error registering client: {str(e)}")
            return False
    
    def _generate_custom_id(self) -> str:
        """Generate a custom ID for the client.
        
        Returns:
            str: The generated custom ID.
        """
        # Use hostname and a random UUID component
        hostname = os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown")
        unique_id = str(uuid.uuid4())[:8]
        
        return f"{hostname}-{unique_id}"
    
    def get_auth_code(self) -> Optional[str]:
        """Get the authentication code for connecting to the app.
        
        Returns:
            Optional[str]: The authentication code, or None if not available.
        """
        return self._auth_code
    
    def display_auth_code(self) -> None:
        """Display the authentication code in a user-friendly format."""
        if not self._auth_code:
            print("\nNo authentication code available. Please register first.\n")
            return
            
        code = self._auth_code
        
        print("\n" + "=" * 40)
        print("    AUTHENTICATION CODE")
        print("=" * 40)
        print(f"\n        {code}")
        print("\nEnter this code in the GrowAssistant app")
        print("to connect this client to your environment.")
        print("\n" + "=" * 40 + "\n")
    
    async def check_connection_status(self) -> Tuple[bool, Literal["not_connected", "connected", "ready"]]:
        """Check if client has been connected to an environment.
        
        Returns:
            Tuple[bool, str]: A tuple containing:
                - True if connected in any form, False otherwise
                - Status string: "not_connected", "connected" (but no space), or "ready" (with space)
        """
        if not self._client_id or not self._client:
            logger.warning("No client ID or client not started")
            return False, "not_connected"
            
        url = f"{self._base_url}/client/{self._client_id}"
        
        try:
            response = await self._client.get(url)
            
            # 204 means client is connected but no space is created yet
            if response.status_code == 204:
                logger.info("Client is connected to the API but no space is created yet")
                
                # Update credentials with connected status
                self._credentials["connected"] = True
                self._auth_code = None  # Clear the code as it's no longer needed
                self._save_credentials()
                
                return True, "connected"
                
            # 200 means client is connected and space is created (ready to send data)
            elif response.status_code == 200:
                logger.info("Client is connected to an environment and space is created")
                
                # Update credentials with connected status and ready flag
                self._credentials["connected"] = True
                self._credentials["ready"] = True
                self._auth_code = None  # Clear the code as it's no longer needed
                self._save_credentials()
                
                # Process response data if needed
                data = response.json()
                
                # Check if the client has been connected to an environment
                # (code is set to null after connecting)
                connected = data.get("code") is None
                
                return connected, "ready"
                
            else:
                logger.warning(f"Error checking connection status: {response.status_code}")
                return False, "not_connected"
                
        except Exception as e:
            logger.error(f"Error checking connection status: {e}")
            return False, "not_connected"
    
    async def wait_for_connection(self, timeout: Optional[float] = None) -> bool:
        """Wait for the client to be connected to an environment (204 or 200).
        
        Args:
            timeout: Timeout in seconds, or None to wait indefinitely.
            
        Returns:
            bool: True if connected within the timeout, False otherwise.
        """
        start_time = asyncio.get_event_loop().time()
        poll_interval = 5  # seconds
        
        while True:
            connected, status = await self.check_connection_status()
            
            if connected:
                return True
                
            # Check if we've exceeded the timeout
            if timeout is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Connection timeout after {elapsed:.1f} seconds")
                    return False
            
            # Wait before checking again
            await asyncio.sleep(poll_interval)
    
    async def wait_for_space_creation(self, timeout: Optional[float] = None) -> bool:
        """Wait for a space to be created for this client (status code 200).
        
        This method polls the API every 30 seconds until it receives a 200 status code,
        indicating that a space has been created and the client is ready to send data.
        
        Args:
            timeout: Timeout in seconds, or None to wait indefinitely.
            
        Returns:
            bool: True if space was created within the timeout, False otherwise.
        """
        start_time = asyncio.get_event_loop().time()
        poll_interval = 30
        
        while True:
            connected, status = await self.check_connection_status()
            
            if status == "ready":
                logger.info("Space created successfully, client is ready to send data")
                return True
                
            # Check if we've exceeded the timeout
            if timeout is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Space creation timeout after {elapsed:.1f} seconds")
                    return False
            
            logger.info(f"Space not created yet, checking again in {poll_interval} seconds")
            # Wait before checking again
            await asyncio.sleep(poll_interval)
    
    def is_ready_for_data(self) -> bool:
        """Check if the client is ready to send data (connected and space created).
        
        Returns:
            bool: True if ready to send data, False otherwise.
        """
        return (self._credentials is not None and 
                self._client_id is not None and 
                self._credentials.get("ready", False))
    
    def get_client_id(self) -> Optional[str]:
        """Get the client ID.
        
        Returns:
            Optional[str]: The client ID, or None if not available.
        """
        return self._client_id


# Create a global instance for easy imports
auth_manager = AuthManager() 
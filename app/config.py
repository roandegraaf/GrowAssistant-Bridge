"""
Configuration Module.

This module handles loading configuration from the config.yaml file,
with support for environment variable overrides.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


logger = logging.getLogger(__name__)


class Config:
    """Configuration manager for the application.
    
    This class loads configuration from a YAML file and provides
    access to configuration values with environment variable overrides.
    
    Attributes:
        _instance: Singleton instance of the Config class.
        config: The loaded configuration dictionary.
        config_file: Path to the configuration file.
    """
    
    _instance = None
    
    def __new__(cls, config_file: Optional[str] = None):
        """Create or return the singleton instance.
        
        Args:
            config_file: Path to the configuration file, or None to use the default.
            
        Returns:
            Config: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config_file: Optional[str] = None):
        """Initialize the configuration manager.
        
        Args:
            config_file: Path to the configuration file, or None to use the default.
        """
        if self._initialized:
            return
            
        self.config_file = config_file or self._find_config_file()
        self.config = {}
        self._initialized = True
        self.load_config()
    
    def _find_config_file(self) -> str:
        """Find the configuration file.
        
        Looks for config.yaml in the current directory, then in the parent directory.
        
        Returns:
            str: Path to the configuration file.
            
        Raises:
            FileNotFoundError: If the configuration file cannot be found.
        """
        # Look in current directory
        if os.path.exists("config.yaml"):
            return "config.yaml"
        
        # Look in parent directory (assuming we're in a package)
        parent_config = os.path.join("..", "config.yaml")
        if os.path.exists(parent_config):
            return parent_config
        
        # Look relative to the script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        app_dir = os.path.dirname(script_dir)
        project_config = os.path.join(app_dir, "config.yaml")
        if os.path.exists(project_config):
            return project_config
            
        raise FileNotFoundError("Could not find config.yaml")
    
    def load_config(self) -> None:
        """Load configuration from the configuration file.
        
        Raises:
            FileNotFoundError: If the configuration file does not exist.
            yaml.YAMLError: If the configuration file is not valid YAML.
        """
        logger.info(f"Loading configuration from {self.config_file}")
        
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")
        
        try:
            with open(self.config_file, "r") as f:
                self.config = yaml.safe_load(f)
                
            if self.config is None:
                self.config = {}
                logger.warning("Configuration file is empty, using defaults")
                
            # Create directories specified in the configuration
            self._create_directories()
                
            logger.info("Configuration loaded successfully")
        except yaml.YAMLError as e:
            logger.error(f"Error parsing configuration file: {e}")
            raise
    
    def _create_directories(self) -> None:
        """Create directories specified in the configuration."""
        # Create log directory if it doesn't exist
        log_file = self.get("general.log_file")
        if log_file:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
                logger.info(f"Created log directory: {log_dir}")
        
        # Create data directory if it doesn't exist
        data_dir = self.get("general.data_dir")
        if data_dir and not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            logger.info(f"Created data directory: {data_dir}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value, with environment variable override.
        
        Args:
            key: The configuration key, using dot notation (e.g., "api.url").
            default: The default value to return if the key is not found.
            
        Returns:
            Any: The configuration value.
        """
        # Check for environment variable override
        env_var = f"CANNABIS_GROW_{key.upper().replace('.', '_')}"
        env_value = os.environ.get(env_var)
        if env_value is not None:
            logger.debug(f"Using environment variable override for {key}: {env_var}")
            return env_value
        
        # Get value from config dict
        parts = key.split(".")
        config = self.config
        for part in parts:
            if not isinstance(config, dict) or part not in config:
                return default
            config = config[part]
        
        return config
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """Get a section of the configuration.
        
        Args:
            section: The section name (top-level key).
            
        Returns:
            Dict[str, Any]: The section dictionary, or an empty dict if not found.
        """
        return self.config.get(section, {})

    def reload(self) -> None:
        """Reload the configuration from the file.
        
        This method is useful when the configuration file has been modified externally.
        
        Raises:
            FileNotFoundError: If the configuration file does not exist.
            yaml.YAMLError: If the configuration file is not valid YAML.
        """
        logger.info(f"Reloading configuration from {self.config_file}")
        self.load_config()
        logger.info("Configuration reloaded successfully")


# Create a global instance for easy imports
config = Config()


def init_logging():
    """Initialize logging based on the configuration."""
    log_level_name = config.get("general.log_level", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    
    log_file = config.get("general.log_file")
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers to prevent duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)
    
    # Create file handler if log file is specified
    if log_file:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)
    
    logger.info(f"Logging initialized at level {log_level_name}") 
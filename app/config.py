"""Configuration management with YAML file and environment variable overrides."""

import logging
import os
from typing import Any, Optional

import yaml

from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class Config(metaclass=SingletonMeta):
    """Configuration manager with YAML file loading and environment overrides.

    Uses SingletonMeta to ensure only one instance exists.
    """

    def __init__(self, config_file: Optional[str] = None):
        """Initialize the configuration manager."""
        self.config_file = config_file or self._find_config_file()
        self.config: dict[str, Any] = {}
        self.load_config()

    def _find_config_file(self) -> str:
        """Find the configuration file in standard locations."""
        search_paths = [
            "config.yaml",
            os.path.join("..", "config.yaml"),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"
            ),
        ]

        for path in search_paths:
            if os.path.exists(path):
                return path

        raise FileNotFoundError("Could not find config.yaml")

    def load_config(self) -> None:
        """Load configuration from the YAML file."""
        logger.info(f"Loading configuration from {self.config_file}")

        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")

        try:
            with open(self.config_file) as f:
                self.config = yaml.safe_load(f) or {}

            if not self.config:
                logger.warning("Configuration file is empty, using defaults")

            self._create_directories()
            logger.info("Configuration loaded successfully")
        except yaml.YAMLError as e:
            logger.error(f"Error parsing configuration file: {e}")
            raise

    def _create_directories(self) -> None:
        """Create directories specified in the configuration."""
        for key in ["general.log_file", "general.data_dir"]:
            path = self.get(key)
            if not path:
                continue

            directory = os.path.dirname(path) if key.endswith("_file") else path
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                logger.info(f"Created directory: {directory}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value with environment variable override.

        Args:
            key: Configuration key using dot notation (e.g., "api.url").
            default: Default value if key is not found.
        """
        # Check for environment variable override
        env_var = f"CANNABIS_GROW_{key.upper().replace('.', '_')}"
        env_value = os.environ.get(env_var)
        if env_value is not None:
            logger.debug(f"Using environment override for {key}: {env_var}")
            return env_value

        # Traverse config dict
        value = self.config
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]

        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """Get a section of the configuration."""
        return self.config.get(section, {})

    def reload(self) -> None:
        """Reload the configuration from file."""
        logger.info(f"Reloading configuration from {self.config_file}")
        self.load_config()


config = Config()


def init_logging():
    """Initialize logging based on the configuration."""
    log_level_name = config.get("general.log_level", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    log_file = config.get("general.log_file")
    log_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(log_format)
        root_logger.addHandler(file_handler)

    logger.info(f"Logging initialized at level {log_level_name}")

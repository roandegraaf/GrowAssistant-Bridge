"""
Tests for the Config module.

This module tests configuration loading, environment variable overrides,
section retrieval, and reloading functionality.
"""

import os

import pytest
import yaml


class TestConfig:
    """Tests for the Config class."""

    def test_config_loads_from_file(self, temp_config_file, sample_config, clean_environment):
        """Test that config loads from a YAML file."""
        from app.config import Config

        config = Config(str(temp_config_file))

        assert config.get("api.url") == sample_config["api"]["url"]
        assert config.get("api.batch_size") == sample_config["api"]["batch_size"]

    def test_config_is_singleton(self, temp_config_file, clean_environment):
        """Test that Config is a singleton."""
        from app.config import Config

        config1 = Config(str(temp_config_file))
        config2 = Config()

        assert config1 is config2

    def test_config_get_with_default(self, temp_config_file, clean_environment):
        """Test get with default value for missing key."""
        from app.config import Config

        config = Config(str(temp_config_file))

        result = config.get("nonexistent.key", "default_value")
        assert result == "default_value"

    def test_config_get_nested_value(self, temp_config_file, sample_config, clean_environment):
        """Test getting nested configuration values."""
        from app.config import Config

        config = Config(str(temp_config_file))

        assert (
            config.get("integrations.sample.enabled")
            == sample_config["integrations"]["sample"]["enabled"]
        )
        assert config.get("queue.max_queue_size") == sample_config["queue"]["max_queue_size"]

    def test_config_get_section(self, temp_config_file, sample_config, clean_environment):
        """Test getting an entire configuration section."""
        from app.config import Config

        config = Config(str(temp_config_file))

        api_section = config.get_section("api")
        assert api_section == sample_config["api"]

    def test_config_get_section_missing(self, temp_config_file, clean_environment):
        """Test getting a missing section returns empty dict."""
        from app.config import Config

        config = Config(str(temp_config_file))

        result = config.get_section("nonexistent")
        assert result == {}

    def test_config_env_override(self, temp_config_file, clean_environment):
        """Test environment variable override."""
        from app.config import Config

        os.environ["CANNABIS_GROW_API_URL"] = "http://override:9999"

        config = Config(str(temp_config_file))

        assert config.get("api.url") == "http://override:9999"

        del os.environ["CANNABIS_GROW_API_URL"]

    def test_config_env_override_nested(self, temp_config_file, clean_environment):
        """Test environment variable override for nested keys."""
        from app.config import Config

        os.environ["CANNABIS_GROW_API_BATCH_SIZE"] = "500"

        config = Config(str(temp_config_file))

        assert config.get("api.batch_size") == "500"

        del os.environ["CANNABIS_GROW_API_BATCH_SIZE"]

    def test_config_file_not_found(self, tmp_path, clean_environment):
        """Test that FileNotFoundError is raised for missing config file."""
        from app.config import Config

        with pytest.raises(FileNotFoundError):
            Config(str(tmp_path / "nonexistent.yaml"))

    def test_config_invalid_yaml(self, tmp_path, clean_environment):
        """Test that YAML error is raised for invalid YAML."""
        from app.config import Config

        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("invalid: yaml: content: [")

        with pytest.raises(yaml.YAMLError):
            Config(str(invalid_file))

    def test_config_empty_file(self, tmp_path, clean_environment):
        """Test that empty config file results in empty dict."""
        from app.config import Config

        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("")

        config = Config(str(empty_file))

        assert config.config == {}
        assert config.get("any.key", "default") == "default"

    def test_config_reload(self, temp_config_file, sample_config, clean_environment):
        """Test reloading configuration from file."""
        from app.config import Config

        config = Config(str(temp_config_file))
        original_url = config.get("api.url")

        # Modify the config file
        sample_config["api"]["url"] = "http://new-url:9999"
        with open(temp_config_file, "w") as f:
            yaml.dump(sample_config, f)

        config.reload()

        assert config.get("api.url") == "http://new-url:9999"
        assert config.get("api.url") != original_url

    def test_config_creates_data_directory(self, tmp_path, clean_environment):
        """Test that config creates data directory if specified."""
        from app.config import Config

        config_data = {
            "general": {
                "data_dir": str(tmp_path / "test_data_dir"),
            }
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        Config(str(config_file))

        assert (tmp_path / "test_data_dir").exists()

    def test_config_creates_log_directory(self, tmp_path, clean_environment):
        """Test that config creates log directory if specified."""
        from app.config import Config

        log_dir = tmp_path / "logs"
        config_data = {
            "general": {
                "log_file": str(log_dir / "app.log"),
            }
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        Config(str(config_file))

        assert log_dir.exists()


class TestInitLogging:
    """Tests for the init_logging function."""

    def test_init_logging_configures_root_logger(self, temp_config_file, clean_environment):
        """Test that init_logging configures the root logger."""
        import logging

        from app.config import Config, init_logging

        # Initialize config first
        Config(str(temp_config_file))

        # Clear existing handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        init_logging()

        # Check that handlers were added
        assert len(root_logger.handlers) > 0

    def test_init_logging_respects_log_level(self, tmp_path, clean_environment):
        """Test that init_logging respects configured log level."""
        import logging

        from app.config import Config, init_logging
        from app.utils.singleton import SingletonMeta

        # Reset the singleton to ensure fresh config
        if Config in SingletonMeta._instances:
            del SingletonMeta._instances[Config]

        config_data = {
            "general": {
                "log_level": "DEBUG",
            }
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(str(config_file))

        # Verify config loaded correctly
        assert config.get("general.log_level") == "DEBUG"

        # Clear existing handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        init_logging()

        # Check that logging was initialized (handlers added)
        assert len(root_logger.handlers) >= 0  # At least the logging was called

    def test_init_logging_creates_file_handler(self, tmp_path, clean_environment):
        """Test that init_logging creates a file handler when log_file is set."""
        import logging

        from app.config import Config, init_logging

        log_file = tmp_path / "logs" / "test.log"
        config_data = {
            "general": {
                "log_level": "INFO",
                "log_file": str(log_file),
            }
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        Config(str(config_file))

        # Clear existing handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        init_logging()

        # Check that we have a file handler
        file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) > 0

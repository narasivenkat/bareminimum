"""Configuration manager module for loading and retrieving application settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from log.log import get_logger

logger = get_logger(__name__)


class ConfigurationError(Exception):
    """Raised when application configuration cannot be loaded or parsed."""

    pass


class ConfigManager:
    """Manages application configuration loaded from a YAML file.

    Loads config.yaml once upon first access of any getter method.
    The YAML file is structured so that top-level keys are "sections" and
    nested keys are "options", mirroring the (section, option) lookup.
    """

    _config: Optional[Dict[str, Dict[str, Any]]] = None
    _config_file_path: Optional[str] = None

    @classmethod
    def get_bareminimum_dir(cls) -> Path:
        """Return the bareminimum source code root directory without hardcoding."""
        for env_var in (
            "BAREMINIMUM_DIR",
            "BAREMINIMUM_HOME",
            "BAREMINIMUM_ROOT",
            "BAREMINIMUM_SOURCE_DIR",
            "BAREMINIMUM_SOURCE",
        ):
            val = os.getenv(env_var)
            if val:
                return Path(val).resolve()
        return Path(__file__).resolve().parent.parent

    @classmethod
    def get_harness_dir(cls) -> Path:
        """Return the bareminimum source code root directory (backward-compatible alias)."""
        return cls.get_bareminimum_dir()

    @classmethod
    def get_config_file_path(cls) -> str:
        """Return the configuration file path."""
        if cls._config_file_path is not None:
            return cls._config_file_path
        for env_var in ("HARNESS_CONFIG", "HARNESS_CONFIG_PATH"):
            val = os.getenv(env_var)
            if val:
                return val
        return str(cls.get_bareminimum_dir() / "resources" / "config.yaml")

    @classmethod
    def set_config_file_path(cls, path: Optional[str]) -> None:
        """Set the configuration file path (useful for testing or custom configs)."""
        cls._config_file_path = path
        cls._config = None

    @classmethod
    def reset(cls) -> None:
        """Reset the cached configuration."""
        cls._config = None
        cls._config_file_path = None

    @classmethod
    def _ensure_config_loaded(cls) -> None:
        """Ensures the configuration is loaded before accessing."""
        if cls._config is None:
            logger.debug("Config not yet loaded. Attempting to load configuration.")
            cls._load_config()

    @classmethod
    def _load_config(cls) -> None:
        """Loads configuration from YAML into class-level _config."""
        config_path = cls.get_config_file_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                if loaded is None:
                    loaded = {}
                if not isinstance(loaded, dict):
                    msg = (
                        f"Configuration file '{config_path}' must contain a "
                        f"mapping at the top level, got {type(loaded).__name__}."
                    )
                    logger.error(msg)
                    raise ConfigurationError(msg)
                cls._config = loaded
                logger.info("Configuration loaded successfully from '%s'.", config_path)
            except yaml.YAMLError as e:
                msg = f"Error parsing YAML configuration file '{config_path}': {e}"
                logger.error(msg)
                raise ConfigurationError(msg) from e
            except ConfigurationError:
                raise
            except Exception as e:
                msg = f"An unexpected error occurred while loading config: {e}"
                logger.error(msg)
                raise ConfigurationError(msg) from e
        else:
            msg = f"Configuration file '{config_path}' not found."
            logger.error(msg)
            logger.error(
                "Please ensure the config.yaml file exists in the 'resources' directory."
            )
            raise ConfigurationError(msg)

    @classmethod
    def get(cls, section: str, option: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves a property value from configuration as a string."""
        cls._ensure_config_loaded()

        if cls._config is None:
            logger.error("ConfigManager._config is unexpectedly None after loading attempt in get().")
            return default

        section_data = cls._config.get(section)
        if section_data is None or not isinstance(section_data, dict):
            logger.warning("Section '%s' not found or invalid in config. Returning default value.", section)
            return default

        if option not in section_data:
            logger.warning("Option '%s' not found in section '%s'. Returning default value.", option, section)
            return default

        value = section_data[option]
        if value is None:
            return default
        return str(value)

    @classmethod
    def get_raw(cls, section: str, option: str, default: Optional[Any] = None) -> Any:
        """Retrieves a property value in its original parsed form."""
        cls._ensure_config_loaded()

        if cls._config is None:
            logger.error("ConfigManager._config is unexpectedly None after loading attempt in get_raw().")
            return default

        section_data = cls._config.get(section)
        if not isinstance(section_data, dict):
            return default
        value = section_data.get(option, default)
        return default if value is None else value

    @classmethod
    def get_int(cls, section: str, option: str, default: Optional[int] = None) -> Optional[int]:
        """Retrieves an integer property value from the configuration."""
        value = cls.get(section, option)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                logger.error(
                    "Config value for '%s.%s' is not a valid integer: '%s'. Returning default.",
                    section, option, value
                )
        return default

    @classmethod
    def get_float(cls, section: str, option: str, default: Optional[float] = None) -> Optional[float]:
        """Retrieves a float property value from the configuration."""
        value = cls.get(section, option)
        if value is not None:
            try:
                return float(value)
            except ValueError:
                logger.error(
                    "Config value for '%s.%s' is not a valid float: '%s'. Returning default.",
                    section, option, value
                )
        return default


__all__ = ["ConfigManager", "ConfigurationError"]

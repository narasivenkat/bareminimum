"""Unit tests for config/config.py."""

import os
import tempfile
import pytest
from pathlib import Path

from config.config import ConfigManager, ConfigurationError


def test_config_manager_get(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
OAuth:
  CLIENT_ID: "test-id"
AGENT:
  MAX_TURNS: 50
""",
        encoding="utf-8",
    )

    ConfigManager.set_config_file_path(str(config_file))
    assert ConfigManager.get("OAuth", "CLIENT_ID") == "test-id"
    assert ConfigManager.get_int("AGENT", "MAX_TURNS") == 50
    assert ConfigManager.get("OAuth", "NON_EXISTENT", "default_val") == "default_val"


def test_config_manager_get_float(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
LLM:
  INPUT_COST_PER_1M: 0.15
  OUTPUT_COST_PER_1M: 0.60
  INVALID_FLOAT: "not_a_float"
""",
        encoding="utf-8",
    )

    ConfigManager.set_config_file_path(str(config_file))
    assert ConfigManager.get_float("LLM", "INPUT_COST_PER_1M") == 0.15
    assert ConfigManager.get_float("LLM", "OUTPUT_COST_PER_1M") == 0.60
    assert ConfigManager.get_float("LLM", "INVALID_FLOAT", 1.23) == 1.23
    assert ConfigManager.get_float("LLM", "MISSING_KEY", 0.99) == 0.99


def test_config_manager_get_raw(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
LLM:
  MODELS:
    - NAME: model-a
    - NAME: model-b
""",
        encoding="utf-8",
    )

    ConfigManager.set_config_file_path(str(config_file))
    models = ConfigManager.get_raw("LLM", "MODELS")
    assert isinstance(models, list)
    assert len(models) == 2
    assert models[0]["NAME"] == "model-a"


def test_config_manager_missing_file(tmp_path):
    non_existent = tmp_path / "non_existent.yaml"
    ConfigManager.set_config_file_path(str(non_existent))

    with pytest.raises(ConfigurationError) as exc_info:
        ConfigManager.get("OAuth", "CLIENT_ID")
    assert "not found" in str(exc_info.value)


def test_config_manager_invalid_yaml(tmp_path):
    invalid_file = tmp_path / "invalid.yaml"
    invalid_file.write_text("::invalid yaml::", encoding="utf-8")
    ConfigManager.set_config_file_path(str(invalid_file))

    with pytest.raises(ConfigurationError) as exc_info:
        ConfigManager.get("OAuth", "CLIENT_ID")
    assert "Error parsing YAML" in str(exc_info.value) or "Failed" in str(exc_info.value)


def test_config_manager_non_dict_yaml(tmp_path):
    invalid_file = tmp_path / "scalar.yaml"
    invalid_file.write_text("just a string", encoding="utf-8")
    ConfigManager.set_config_file_path(str(invalid_file))

    with pytest.raises(ConfigurationError) as exc_info:
        ConfigManager.get("OAuth", "CLIENT_ID")
    assert "must contain a mapping" in str(exc_info.value)


def test_get_bareminimum_dir_and_config_path_default():
    ConfigManager.reset()
    bareminimum_dir = ConfigManager.get_bareminimum_dir()
    harness_dir = ConfigManager.get_harness_dir()
    expected_root = Path(__file__).resolve().parent.parent
    assert bareminimum_dir == expected_root
    assert harness_dir == expected_root

    config_path = ConfigManager.get_config_file_path()
    assert config_path == str(expected_root / "resources" / "config.yaml")


def test_get_bareminimum_dir_and_config_path_env_var(monkeypatch, tmp_path):
    ConfigManager.reset()
    custom_dir = tmp_path / "custom_bareminimum"
    custom_dir.mkdir()
    monkeypatch.setenv("BAREMINIMUM_DIR", str(custom_dir))

    assert ConfigManager.get_bareminimum_dir() == custom_dir.resolve()
    assert ConfigManager.get_harness_dir() == custom_dir.resolve()
    assert ConfigManager.get_config_file_path() == str(custom_dir.resolve() / "resources" / "config.yaml")


def test_get_config_file_path_direct_env_var(monkeypatch, tmp_path):
    ConfigManager.reset()
    custom_config = tmp_path / "custom_config.yaml"
    monkeypatch.setenv("HARNESS_CONFIG", str(custom_config))

    assert ConfigManager.get_config_file_path() == str(custom_config)

"""Load and validate shared workspace configuration."""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_config():
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a mapping: {CONFIG_PATH}")
    return config


CONFIG = _load_config()


def section(name):
    value = CONFIG.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Missing or invalid '{name}' section in {CONFIG_PATH}")
    return value

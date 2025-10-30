"""Configuration helpers for loading YAML files with environment expansion."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a YAML configuration file and expand environment variables.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Parsed configuration dictionary. Returns an empty dict if the file is
        empty.
    """

    config_path = Path(path)
    raw_text = config_path.read_text()
    expanded = os.path.expandvars(raw_text)
    data = yaml.safe_load(expanded) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Config root must be a mapping, got {type(data)!r}")
    return dict(data)


__all__ = ["load_config"]

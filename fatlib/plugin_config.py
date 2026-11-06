from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any


DEFAULT_CONFIG_FILE = Path.home() / ".fat" / "config.json"
DEFAULT_PLUGIN_DIR = Path.home() / ".fat" / "plugins"
PLUGIN_OPTIONS = ("--config-file", "--plugin-dir")


def _option_value(argv: list[str], option: str) -> str | None:
    for index, value in enumerate(argv):
        if value == option:
            if index + 1 >= len(argv):
                raise SystemExit(f"{option} requires a value")
            return argv[index + 1]
    return None


def resolve_config_file(argv: list[str]) -> Path:
    value = _option_value(argv, "--config-file")
    return Path(value).expanduser() if value else DEFAULT_CONFIG_FILE


def load_config(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SystemExit(f"FAT config file must be a regular, non-symlink file: {path}")
    if not path.exists():
        return {}
    if not path.is_file():
        raise SystemExit(f"FAT config file must be a regular, non-symlink file: {path}")
    metadata = path.stat()
    if metadata.st_uid not in {os.geteuid(), 0} or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SystemExit(f"FAT config file has unsafe ownership or permissions: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read FAT config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"FAT config file must contain a JSON object: {path}")
    return data


def resolve_plugin_dir(argv: list[str]) -> Path:
    explicit = _option_value(argv, "--plugin-dir")
    if explicit is not None:
        if not explicit.strip():
            raise SystemExit("--plugin-dir requires a non-empty path")
        return Path(explicit).expanduser()

    config = load_config(resolve_config_file(argv))
    configured = config.get("plugin_dir")
    if configured is None:
        return DEFAULT_PLUGIN_DIR
    if not isinstance(configured, str) or not configured.strip():
        raise SystemExit("FAT config value `plugin_dir` must be a non-empty string")
    return Path(configured).expanduser()


def strip_plugin_options(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in PLUGIN_OPTIONS:
            if index + 1 >= len(argv):
                raise SystemExit(f"{token} requires a value")
            skip_next = True
            continue
        cleaned.append(token)
    return cleaned

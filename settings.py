"""Persisted app settings (HTTP listen port, …)."""

from __future__ import annotations

import json
from typing import Any

from paths import data_file

DEFAULT_PORT = 8080
PORT_MIN = 1024
PORT_MAX = 65535


def _path(*, write: bool = False):
    return data_file("settings.json", write=write)


def load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save(data: dict[str, Any]) -> None:
    path = _path(write=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def clamp_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port < PORT_MIN or port > PORT_MAX:
        return None
    return port


def listen_port(*, env_port: str | None = None) -> int:
    """Prefer saved settings, then EFB_PORT, then default."""
    saved = clamp_port(load().get("port"))
    if saved is not None:
        return saved
    if env_port not in (None, ""):
        env = clamp_port(env_port)
        if env is not None:
            return env
    return DEFAULT_PORT


def set_port(port: int) -> dict[str, Any]:
    data = load()
    data["port"] = int(port)
    save(data)
    return data
